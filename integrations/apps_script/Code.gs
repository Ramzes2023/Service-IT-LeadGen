/**
 * Service IT — Gmail to V2, INDEPENDENT of ChatGPT.
 * Owner: standalone Google Apps Script (V8), Advanced Gmail API v1.
 * No messages are modified; no Bitrix24 REST writes; no unsafe logging.
 *
 * Initial mode: checkB2BCenter() is READ-ONLY. Forwarding requires explicit
 * Script Properties + installPollingTrigger() by the Gmail account owner.
 */
'use strict';

const SERVICEIT = Object.freeze({
  mailbox: 'ramzesisayev444@gmail.com',
  sender: 'marketplace@b2b-center.ru',
  query: 'from:marketplace@b2b-center.ru newer_than:30d -in:spam -in:trash',
  maxPages: 10,           // stop after 1,000 recent messages, never skip silently
  pageSize: 100,
  maxRawCharacters: 350000, // approx <=256 KiB raw MIME
  receiverPath: '/api/v2/gmail/ingest',
  schema: 'serviceit-appsscript-v1',
  triggerMinutes: 5
});

function assertMailbox_() {
  const result = Gmail.Users.getProfile('me');
  const actual = String(result.emailAddress || '').toLowerCase();
  if (actual !== SERVICEIT.mailbox) throw new Error('WRONG_GMAIL_ACCOUNT');
}

function listVendorIds_() {
  const ids = [];
  const seen = {};
  let page = null;
  for (let i = 0; i < SERVICEIT.maxPages; i++) {
    const options = {q: SERVICEIT.query, maxResults: SERVICEIT.pageSize,
                     includeSpamTrash: false};
    if (page) options.pageToken = page;
    const result = Gmail.Users.Messages.list('me', options);
    const rows = result.messages || [];
    for (let j = 0; j < rows.length; j++) {
      const id = String(rows[j].id || '');
      if (!/^[a-f0-9]{8,24}$/.test(id)) throw new Error('INVALID_GMAIL_MESSAGE_ID');
      if (!seen[id]) { seen[id] = true; ids.push(id); }
    }
    page = result.nextPageToken || null;
    if (!page) return ids;
  }
  throw new Error('BACKLOG_OVER_1000_MESSAGES_REQUIRES_RECONCILIATION');
}

/** Safe, user-run first test: no third-party network request. */
function checkB2BCenter() {
  assertMailbox_();
  const ids = listVendorIds_();
  console.log('SERVICEIT_MODE=READ_ONLY');
  console.log('B2B_VENDOR_MESSAGES_WITHIN_30_DAYS=' + ids.length);
  console.log('EXTERNAL_TRANSMISSION=0');
  console.log('CRM_WRITES=0');
}

/** A truthful preview. Does not print URL, key, email MIME or message IDs. */
function serviceITStatus() {
  const p = PropertiesService.getScriptProperties();
  const url = String(p.getProperty('SERVICEIT_RECEIVER_URL') || '');
  const key = String(p.getProperty('SERVICEIT_HMAC_SECRET') || '');
  const configured = isSafeEndpoint_(url) && key.length >= 32;
  const installed = ScriptApp.getProjectTriggers().filter(function(t) {
    return t.getHandlerFunction() === 'pollAndForwardB2B';
  }).length;
  console.log('SERVICEIT_RECEIVER_CONFIGURED=' + configured);
  console.log('SERVICEIT_FORWARDING_ENABLED=' +
              (p.getProperty('SERVICEIT_SEND_ENABLED') === 'YES'));
  console.log('SERVICEIT_TRIGGER_COUNT=' + installed);
  console.log('SERVICEIT_LAST_RESULT=' +
              (p.getProperty('SERVICEIT_LAST_RESULT') || 'NOT_STARTED'));
}

function isSafeEndpoint_(url) {
  if (typeof url !== 'string' || url.length < 28 || url.length > 350) return false;
  const m = /^https:\/\/([a-z0-9][a-z0-9.-]{2,240})\/api\/v2\/gmail\/ingest$/.exec(url);
  if (!m) return false;
  const host = m[1];
  return !(/^(localhost|127\.|10\.|192\.168\.|172\.|0\.|169\.254\.|::1)/.test(host) ||
           host.endsWith('.local') || host.endsWith('.internal') ||
           host.includes('..') || host.startsWith('-') || host.endsWith('-'));
}

function getReceiver_() {
  const p = PropertiesService.getScriptProperties();
  const url = String(p.getProperty('SERVICEIT_RECEIVER_URL') || '');
  const secret = String(p.getProperty('SERVICEIT_HMAC_SECRET') || '');
  if (!isSafeEndpoint_(url)) throw new Error('RECEIVER_HTTPS_URL_NOT_CONFIGURED');
  if (secret.length < 32 || secret.length > 512 || /[\r\n]/.test(secret))
    throw new Error('HMAC_SECRET_NOT_CONFIGURED');
  if (p.getProperty('SERVICEIT_SEND_ENABLED') !== 'YES')
    throw new Error('FORWARDING_NOT_EXPLICITLY_ENABLED');
  return {url: url, secret: secret};
}

function hex_(bytes) {
  return bytes.map(function(x) {return ('0' + (x & 255).toString(16)).slice(-2);}).join('');
}

/** Receiver must ack storage BEFORE replying 200/202; no local "processed" flag. */
function deliverOne_(id, raw, receiver) {
  if (!raw || raw.length > SERVICEIT.maxRawCharacters ||
      !/^[a-zA-Z0-9_-]+={0,2}$/.test(raw))
    throw new Error('UNSAFE_OR_OVERSIZE_GMAIL_MIME');
  const payload = JSON.stringify({
    version: SERVICEIT.schema,
    mailbox: SERVICEIT.mailbox,
    gmail_message_id: id,
    raw_b64url: raw
  });
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = hex_(Utilities.computeHmacSha256Signature(
    timestamp + '.' + payload, receiver.secret, Utilities.Charset.UTF_8));
  // Do not allow redirects: a 302 must NEVER forward sensitive MIME to a new host.
  const response = UrlFetchApp.fetch(receiver.url, {
    method: 'post', contentType: 'application/json',
    payload: payload, muteHttpExceptions: true,
    followRedirects: false, validateHttpsCertificates: true,
    headers: {
      'X-ServiceIT-Timestamp': timestamp,
      'X-ServiceIT-Signature': 'sha256=' + signature
    }
  });
  const status = response.getResponseCode();
  if (status !== 200 && status !== 202)
    throw new Error('RECEIVER_HTTP_' + status);
  let ack;
  try { ack = JSON.parse(response.getContentText()); }
  catch (e) { throw new Error('RECEIVER_ACK_NOT_JSON'); }
  if (!ack || (ack.status !== 'INGESTED' && ack.status !== 'ALREADY_INGESTED')
      || ack.gmail_message_id !== id || ack.crm_writes !== 0)
    throw new Error('RECEIVER_ACK_INVALID');
  return ack.status;
}

/**
 * Runs on installable trigger only after explicit setup.
 * Server-side DB unique key guarantees dedup; deliberately no local ack state.
 */
function pollAndForwardB2B() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) throw new Error('POLL_ALREADY_RUNNING');
  try {
    const receiver = getReceiver_();
    assertMailbox_();
    // Process in earliest-first order; errors remain retryable on next run.
    const ids = listVendorIds_().reverse();
    let accepted = 0;
    let duplicates = 0;
    const cache = CacheService.getScriptCache();
    for (let i = 0; i < ids.length; i++) {
      // Optimization only: authoritative deduplication is PostgreSQL.
      if (cache.get('serviceit_ack_' + ids[i])) {
        duplicates++;
        continue;
      }
      const msg = Gmail.Users.Messages.get('me', ids[i], {format: 'raw'});
      if (msg.id !== ids[i]) throw new Error('GMAIL_ID_RESPONSE_MISMATCH');
      const result = deliverOne_(ids[i], String(msg.raw || ''), receiver);
      // Cache ONLY after the server has acknowledged a durable DB transaction.
      cache.put('serviceit_ack_' + ids[i], '1', 21600);
      if (result === 'INGESTED') accepted++;
      else duplicates++;
    }
    PropertiesService.getScriptProperties().setProperty(
      'SERVICEIT_LAST_RESULT',
      'OK total=' + ids.length + ' new=' + accepted + ' duplicate=' + duplicates +
      ' at=' + new Date().toISOString());
    console.log('SERVICEIT_INGEST_OK=' + accepted +
                ' SERVICEIT_DUPLICATE=' + duplicates +
                ' CRM_WRITES=0');
  } catch (error) {
    // Only a bounded internal code; do not disclose HTTP responses or tokens.
    PropertiesService.getScriptProperties().setProperty(
      'SERVICEIT_LAST_RESULT', 'BLOCKED at=' + new Date().toISOString());
    console.error('SERVICEIT_POLL_FAILED_CHECK_EXECUTIONS');
    throw new Error('SERVICEIT_POLL_BLOCKED');
  } finally {
    lock.releaseLock();
  }
}

/** User-run once, only AFTER receiver HTTPS + HMAC key are configured. */
function installPollingTrigger() {
  getReceiver_();
  assertMailbox_();
  const current = ScriptApp.getProjectTriggers().filter(function(t) {
    return t.getHandlerFunction() === 'pollAndForwardB2B';
  });
  if (current.length > 0) {
    console.log('SERVICEIT_TRIGGER_ALREADY_EXISTS=' + current.length);
    return;
  }
  ScriptApp.newTrigger('pollAndForwardB2B')
    .timeBased().everyMinutes(SERVICEIT.triggerMinutes).create();
  console.log('SERVICEIT_TRIGGER_INSTALLED=5_MINUTES');
}

/** Disable THIS script's timer without touching unrelated Google triggers. */
function stopPollingTrigger() {
  ScriptApp.getProjectTriggers().forEach(function(t) {
    if (t.getHandlerFunction() === 'pollAndForwardB2B')
      ScriptApp.deleteTrigger(t);
  });
  PropertiesService.getScriptProperties().setProperty(
    'SERVICEIT_SEND_ENABLED', 'NO');
  console.log('SERVICEIT_TRIGGER_STOPPED');
}
