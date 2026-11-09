/**
 * Service IT — existing Gmail Apps Script -> signed VPS PostgreSQL source ingress.
 *
 * Reads only B2B-Center vendor notification metadata from the owner's Gmail.
 * Sends a minimal signed tender notice (subject, organizer, URL, tender ID,
 * Gmail message ID) directly to VPS. Raw email/MIME, contacts,
 * phone numbers, INN and attachments are NEVER transmitted.
 *
 * The VPS worker creates a guarded 1062/21 HOLD card and queues RouterAI.
 * No Score, outreach, contact, stage move or qualified-lead status is created.
 *
 * Secrets are injected into a private, owner-only secrets.gs by the one-time
 * PowerShell installer. No credentials belong in this tracked file.
 */
const SERVICEIT_CONFIG = Object.freeze({
  // GitHub is CODE/CI only. Direct signed source events go to VPS.
  repository: "Ramzes2023/Service-IT-LeadGen",
  eventType: "serviceit_b2b_notice",
  mailbox: "ramzesisayev444@gmail.com",
  senderDomain: "b2b-center.ru",
  gmailQuery: "from:(b2b-center.ru) newer_than:30d -in:spam -in:trash",
  maxPages: 10,
  pageSize: 100,
  maxMessagesPerCycle: 20,
  // Direct signed VPS receives batches; GitHub Actions is NOT an event
  // transport. Keep a bounded per-trigger budget; any remaining tenders
  // stay PENDING for the next automatic five-minute execution.
  maxDispatchesPerCycle: 4,
  maxTendersPerEmail: 80,
  maxEmailText: 256000,
  triggerMinutes: 5,
  knownSourceStates: {
    "4661617": "RETIRED_DO_NOT_RECREATE",
    "4665948": "EXISTS_DO_NOT_RECREATE"
  }
});

function privateConfig_() {
  if (typeof SERVICEIT_PRIVATE_CONFIG === "undefined" ||
      !SERVICEIT_PRIVATE_CONFIG ||
      SERVICEIT_PRIVATE_CONFIG.ownerApproval !== "YES" ||
      SERVICEIT_PRIVATE_CONFIG.expectedMailbox !== SERVICEIT_CONFIG.mailbox ||
      typeof SERVICEIT_PRIVATE_CONFIG.githubToken !== "string" ||
      SERVICEIT_PRIVATE_CONFIG.githubToken.length < 20 ||
      typeof SERVICEIT_PRIVATE_CONFIG.hmacSecret !== "string" ||
      SERVICEIT_PRIVATE_CONFIG.hmacSecret.length < 32 ||
      !Number.isFinite(Number(SERVICEIT_PRIVATE_CONFIG.cutoverMs)) ||
      Number(SERVICEIT_PRIVATE_CONFIG.cutoverMs) <= 0) {
    throw new Error("SERVICEIT_PRIVATE_CONFIG_BLOCKED");
  }
  return SERVICEIT_PRIVATE_CONFIG;
}

function scriptProperties_() {
  return PropertiesService.getScriptProperties();
}

function hmacHex_(value, key) {
  const bytes = Utilities.computeHmacSha256Signature(
    String(value), String(key), Utilities.Charset.UTF_8
  );
  return bytes.map(function (b) {
    return ("0" + ((b + 256) % 256).toString(16)).slice(-2);
  }).join("");
}

function safeText_(value, maximum) {
  return typeof value === "string" &&
    value.length > 0 && value.length <= maximum &&
    !/[\x00-\x1f\x7f]/.test(value) &&
    !/https?:\/\/|mailto:|@|\{\{|\}\}/i.test(value);
}

function assertMailbox_() {
  const profile = Gmail.Users.getProfile("me");
  if (!profile || String(profile.emailAddress || "").toLowerCase() !==
      SERVICEIT_CONFIG.mailbox) {
    throw new Error("SERVICEIT_GMAIL_IDENTITY_BLOCKED");
  }
  return profile.emailAddress;
}

function listVendorMessageIds_() {
  const ids = [];
  const seen = {};
  let pageToken = null;
  for (let page = 0; page < SERVICEIT_CONFIG.maxPages; page++) {
    const options = {
      q: SERVICEIT_CONFIG.gmailQuery,
      maxResults: SERVICEIT_CONFIG.pageSize,
      includeSpamTrash: false
    };
    if (pageToken) options.pageToken = pageToken;
    const response = Gmail.Users.Messages.list("me", options);
    const messages = response && Array.isArray(response.messages) ?
      response.messages : [];
    messages.forEach(function (message) {
      const id = message && message.id;
      if (typeof id !== "string" || !/^[a-f0-9]{16,24}$/i.test(id)) {
        throw new Error("SERVICEIT_GMAIL_MESSAGE_ID_INVALID");
      }
      if (!seen[id]) {
        seen[id] = true;
        ids.push(id);
      }
    });
    pageToken = response && response.nextPageToken ?
      String(response.nextPageToken) : null;
    if (!pageToken) return ids;
  }
  if (pageToken) throw new Error("SERVICEIT_GMAIL_BACKLOG_OVER_1000_REVIEW");
  return ids;
}

function decodePart_(data) {
  if (typeof data !== "string" || !data) return "";
  const bytes = Utilities.base64DecodeWebSafe(data);
  return Utilities.newBlob(bytes).getDataAsString("UTF-8");
}

function collectParts_(part, output) {
  if (!part || typeof part !== "object" || part.filename) return;
  const mime = String(part.mimeType || "").toLowerCase();
  const body = part.body || {};
  if (body.data && (mime === "text/plain" || mime === "text/html")) {
    const value = decodePart_(body.data);
    if (value) output[mime === "text/plain" ? "plain" : "html"].push(value);
  }
  (Array.isArray(part.parts) ? part.parts : []).forEach(function (child) {
    collectParts_(child, output);
  });
}

function htmlToText_(html) {
  return String(html)
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, " ")
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, " ")
    .replace(/<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi,
      function (_all, href, label) { return " " + label + " [" + href + "] "; })
    .replace(/<br\s*\/?>|<\/(?:p|div|tr|td|li|h1|h2|h3)>/gi, "\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&#(\d+);/g, function (_all, code) {
      const n = Number(code);
      return n > 0 && n <= 0x10ffff ? String.fromCodePoint(n) : " ";
    })
    .replace(/[ \t]+/g, " ")
    .replace(/\n[ \t]+/g, "\n");
}

function messageText_(message) {
  const output = {plain: [], html: []};
  collectParts_(message.payload || {}, output);
  const candidates = output.plain.concat(output.html.map(htmlToText_));
  const text = candidates.sort(function (a, b) {
    return (b.match(/b2b-center\.ru/gi) || []).length -
      (a.match(/b2b-center\.ru/gi) || []).length;
  })[0] || "";
  if (!text || text.length > SERVICEIT_CONFIG.maxEmailText) {
    throw new Error("SERVICEIT_EMAIL_CONTENT_INVALID");
  }
  return text;
}

function messageHeaders_(message) {
  const headers = {};
  const list = message && message.payload && Array.isArray(message.payload.headers) ?
    message.payload.headers : [];
  list.forEach(function (h) {
    if (h && typeof h.name === "string" && typeof h.value === "string") {
      headers[h.name.toLowerCase()] = h.value;
    }
  });
  return headers;
}

function cleanTenderUrl_(raw) {
  if (typeof raw !== "string" || raw.length > 2200) return null;
  const value = raw.replace(/[.,;\)\]}»>'"]+$/g, "");
  if (value.indexOf("#") >= 0) return null;
  const match = value.match(/^https:\/\/(?:www\.)?b2b-center\.ru(?::443)?(\/[^?\s]*)?(?:\?[^#\s]*)?$/i);
  if (!match || !match[1]) return null;
  const path = match[1];
  const tender = path.match(/^\/(?:app\/market-next|market)\/[a-z0-9-]{1,160}\/tender-(\d{5,12})\/$|^\/search\/number\/l(\d{5,12})(?:-\d+){1,8}\/?$/i);
  if (!tender) return null;
  return {
    url: "https://www.b2b-center.ru" + path,
    tenderId: tender[1] || tender[2]
  };
}

function getField_(block, name) {
  const escaped = name.replace(/[.*+?^|()[\]\\]/g, "\\$&");
  const re = new RegExp(escaped + "\\s*:\\s*(.+?)\\s*(?:\\n|$)", "i");
  const match = String(block).match(re);
  if (!match) return null;
  return match[1]
    .replace(/\s*\[https?:\/\/[^\]]+\]/gi, "")
    .trim().replace(/\s+/g, " ").slice(0, 320);
}

function classifyServices_(title) {
  if (typeof title !== "string") return [];
  // JavaScript \w is ASCII-only; Unicode letter classes are required for
  // Russian morphology (техническому, обслуживанию, серверов, базы данных).
  const WORD = "[\\p{L}\\p{N}_]";
  const suffix = WORD + "*";
  const patterns = {
    "1C": new RegExp("(?<!" + WORD + ")1[сc](?!" + WORD + ")|1[сc]:", "iu"),
    "IT_SUPPORT": new RegExp(
      "(?<!" + WORD + ")(?:ит|it)[\\s-]*(?:аутсорс" + suffix + "|поддержк" + suffix + "|обслуживан" + suffix + "|инфраструктур" + suffix + ")" +
      "|(?:технич" + suffix + "|сервисн" + suffix + ")\\s+(?:поддержк" + suffix + "|обслуживан" + suffix + ").{0,80}(?:сервер" + suffix + "|сетев" + suffix + "|компьютер" + suffix + "|информационн" + suffix + "\\s+систем" + suffix + "|(?:ит|it)[\\s-]*инфраструктур" + suffix + ")" +
      "|(?:технич" + suffix + "|сервисн" + suffix + ")\\s+(?:обслуживан" + suffix + "|поддержк" + suffix + ")\\s+(?:баз" + suffix + "\\s+данн" + suffix + "|субд)",
      "iu"
    ),
    "CRM": new RegExp("битрикс[\\s-]*24|(?<!" + WORD + ")(?:crm|срм)(?!" + WORD + ")|amocrm", "iu"),
    "KKT_EDO": new RegExp(
      "(?<!" + WORD + ")(?:ккт|эдо)(?!" + WORD + ")|онлайн[\\s-]*касс" + suffix +
      "|контрольно[\\s-]*кассов" + suffix + "|кассов" + suffix + "\\s+(?:оборудован" + suffix + "|аппарат" + suffix + "|техник" + suffix + "|систем" + suffix +
      ")|криптопро|диадок|электронн" + suffix + "\\s+документооборот" + suffix +
      "|маркировк" + suffix + "\\s+(?:товар" + suffix + "|продукц" + suffix + "|остатк" + suffix +
      ")|торгов" + suffix + "\\s+оборудован" + suffix + "|тсд",
      "iu"
    ),
    "SERVERS": new RegExp(
      "сервер" + suffix + "|сетев" + suffix + "\\s+оборудован" + suffix +
      "|коммутатор" + suffix + "|маршрутизатор" + suffix + "|ноутбук" + suffix +
      "|компьютер" + suffix + "|рабоч" + suffix + "\\s+станци" + suffix +
      "|монитор(?:ы|ов|ам|ами|е|а)?",
      "iu"
    )
  };
  const result = [];
  Object.keys(patterns).forEach(function (key) {
    if (patterns[key].test(title)) result.push(key);
  });
  const context = new RegExp(
    "(?<!" + WORD + ")1[сc](?!" + WORD + ")|1[сc]:|битрикс[\\s-]*24|" +
    "(?<!" + WORD + ")(?:crm|срм|api|erp|wms|эдо|ккт|ит|it|субд)(?!" + WORD + ")|" +
    "программ" + suffix + "\\s+обеспечен" + suffix +
    "|информационн" + suffix + "\\s+систем" + suffix +
    "|баз" + suffix + "\\s+данн" + suffix +
    "|сайт" + suffix + "|веб[\\s-]*приложен" + suffix +
    "|интернет[\\s-]*магазин" + suffix +
    "|сервер" + suffix + "|сетев" + suffix + "\\s+инфраструктур" + suffix,
    "iu"
  );
  const integration = new RegExp(
    "внедрен" + suffix + "|интеграц" + suffix + "|доработк" + suffix +
    "|обмен" + suffix + "\\s+данными|экстрактор" + suffix,
    "iu"
  );
  if (context.test(title) && integration.test(title)) result.push("INTEGRATION");
  return result.sort();
}

function parseEmailMessage_(message, messageId) {
  const headers = messageHeaders_(message);
  const from = String(headers.from || "");
  const senderMatch = from.match(/<?([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})>?/i);
  if (!senderMatch || senderMatch[1].toLowerCase() !== "marketplace@b2b-center.ru") {
    throw new Error("SERVICEIT_SENDER_NOT_VENDOR");
  }
  // These are Gmail MX authentication headers, not vendor-supplied claims.
  const auth = String(headers["authentication-results"] || "");
  if (!/mx\.google\.com\s*;/i.test(auth) ||
      !/\bdkim=pass\b/i.test(auth) ||
      !/\bheader\.i=@b2b-center\.ru\b/i.test(auth) ||
      !/\bdmarc=pass\b/i.test(auth) ||
      !/\bheader\.from=b2b-center\.ru\b/i.test(auth)) {
    throw new Error("SERVICEIT_GMAIL_DKIM_DMARC_NOT_VERIFIED");
  }
  const internalDate = Number(message.internalDate || 0);
  if (!Number.isFinite(internalDate) || internalDate <= 0) {
    throw new Error("SERVICEIT_GMAIL_INTERNAL_DATE_INVALID");
  }
  const text = messageText_(message);
  const hasInvite = text.indexOf("Организатор закупки:") >= 0 &&
    text.indexOf("Предмет закупки:") >= 0;
  const chunks = hasInvite ?
    text.split(/(?=Организатор\s+закупки\s*:)/i).filter(function (x) {
      return /Предмет закупки\s*:/i.test(x);
    }) :
    text.split(/(?=(?:Запрос\s+цен|Запрос\s+предложений|Конкурс|Предварительный\s+квалификационный\s+отбор|Процедура\s+закупки)\s*№\s*\d{5,12})/i)
      .filter(function (x) { return /Организатор\s*:/i.test(x) && /Предмет договора\s*:/i.test(x); });
  const urls = /https:\/\/[^\s<>'"\[\]]{1,2000}/ig;
  const found = {};
  chunks.slice(0, SERVICEIT_CONFIG.maxTendersPerEmail).forEach(function (block) {
    const candidates = [];
    let match;
    while ((match = urls.exec(block)) !== null) {
      const cleaned = cleanTenderUrl_(match[0]);
      if (cleaned) candidates.push(cleaned);
    }
    urls.lastIndex = 0;
    if (!candidates.length) return;
    const title = getField_(block, "Предмет закупки") ||
      getField_(block, "Предмет договора");
    if (!title || !safeText_(title, 240)) return;
    const organizer = getField_(block, "Организатор закупки") ||
      getField_(block, "Организатор") || "НЕ УСТАНОВЛЕН";
    if (!safeText_(organizer, 120)) return;
    const head = block.match(/(?:Запрос\s+цен|Запрос\s+предложений|Конкурс|Предварительный\s+квалификационный\s+отбор|Процедура\s+закупки)\s*№\s*(\d{5,12})/i);
    const selected = (head && candidates.find(function (x) { return x.tenderId === head[1]; })) || candidates[0];
    const services = classifyServices_(title);
    if (!services.length) return;
    const notice = {
      schema: "serviceit-b2b-notice-v1",
      source: "B2B-Center",
      email_message_id: messageId,
      tender_id: selected.tenderId,
      source_url: selected.url,
      subject: title,
      organizer: organizer,
      buyer_inn: null,
      fns_msp: null,
      region: null,
      original_publication_verified: false,
      contact_approved: false,
      qualified_lead: false
    };
    found[selected.tenderId] = {notice: notice, services: services};
  });
  return {
    internalDate: internalDate,
    observations: Object.keys(found).sort().map(function (key) { return found[key]; })
  };
}

function dispatchNotice_(notice, dispatchId) {
  const config = privateConfig_();
  const props = scriptProperties_();
  if (props.getProperty("SERVICEIT_VPS_B2B_DIRECT_ENABLED") !== "YES") {
    throw new Error("SERVICEIT_VPS_B2B_DIRECT_NOT_APPROVED");
  }
  const activation = Number(props.getProperty("SERVICEIT_VPS_B2B_ACTIVATION_MS") || 0);
  if (!Number.isFinite(activation) || activation <= Number(config.cutoverMs)) {
    throw new Error("SERVICEIT_VPS_B2B_ACTIVATION_MISSING");
  }
  const timestamp = String(Math.floor(Date.now() / 1000));
  const payloadJson = JSON.stringify({
    version: 1,
    mailbox: SERVICEIT_CONFIG.mailbox,
    email_message_id: notice.email_message_id,
    dispatch_id: dispatchId,
    notice: notice
  });
  const signature = "sha256=" + hmacHex_(timestamp + "." + payloadJson, config.hmacSecret);
  // Reuses EXISTING Apps Script HMAC. No GitHub dispatch, token or CRM REST.
  const destination = "https://leadgen.serviseit.ru/integrations/serviceit/b2b-center/notice";
  const response = UrlFetchApp.fetch(destination, {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({
      timestamp: timestamp, payload_json: payloadJson, signature: signature
    }),
    followRedirects: false,
    validateHttpsCertificates: true,
    muteHttpExceptions: true,
  });
  if (!response || ![200, 202].includes(response.getResponseCode())) {
    throw new Error("SERVICEIT_VPS_NOTICE_ACK_UNVERIFIED");
  }
  return dispatchId;
}

function processMessage_(messageId, remainingDispatchBudget) {
  const props = scriptProperties_();
  const emailKey = "SERVICEIT_EMAIL_" + messageId;
  const current = props.getProperty(emailKey) || "";
  if (current === "BASELINE" || current === "DONE" || current === "NO_MATCH" ||
      current === "REVIEW_REQUIRED") return {sent: 0, skipped: 1};
  const message = Gmail.Users.Messages.get("me", messageId, {format: "full"});
  if (!message || message.id !== messageId) {
    throw new Error("SERVICEIT_GMAIL_READBACK_ID_MISMATCH");
  }
  const parsed = parseEmailMessage_(message, messageId);
  const cutoverMs = Math.max(Number(privateConfig_().cutoverMs),
    Number(props.getProperty('SERVICEIT_VPS_B2B_ACTIVATION_MS') || 0));
  if (!Number.isFinite(cutoverMs) || cutoverMs <= 0) {
    throw new Error('SERVICEIT_VPS_B2B_ACTIVATION_MISSING');
  }
  if (parsed.internalDate <= cutoverMs) {
    props.setProperty(emailKey, "BASELINE");
    return {sent: 0, skipped: 1};
  }
  let sent = 0;
  let matched = 0;
  let uncertain = 0;
  let deferred = 0;
  for (const observation of parsed.observations) {
    matched++;
    const notice = observation.notice;
    const sourceKey = "SERVICEIT_SOURCE_" + notice.tender_id;
    const state = props.getProperty(sourceKey) || "";
    if (state) continue;
    if (sent + uncertain >= remainingDispatchBudget) {
      deferred++;
      continue;
    }
    const dispatchId = Utilities.getUuid();
    // Reserve before the external VPS POST. Ambiguous sends are never
    // blindly replayed: the VPS may already have persisted the notice.
    props.setProperty(sourceKey, "RESERVED:" + dispatchId);
    try {
      dispatchNotice_(notice, dispatchId);
      props.setProperty(sourceKey, "DISPATCHED:" + dispatchId);
      sent++;
    } catch (_error) {
      props.setProperty(sourceKey, "DISPATCH_UNCERTAIN:" + dispatchId);
      uncertain++;
      // Continue with other tender IDs in this same email. The uncertain
      // source remains fenced and is never re-dispatched.
    }
  }
  if (matched === 0) {
    props.setProperty(emailKey, "NO_MATCH");
  } else if (uncertain > 0) {
    props.setProperty(emailKey, "PARTIAL_REVIEW");
  } else if (deferred > 0) {
    // Never mark an email DONE when its remaining tender IDs still need
    // bounded dispatch in a future five-minute trigger.
    props.setProperty(emailKey, "PENDING");
  } else {
    props.setProperty(emailKey, "DONE");
  }
  return {sent: sent, skipped: matched - sent - deferred,
          uncertain: uncertain, deferred: deferred};
}

function pollAndDispatchB2B() {
  privateConfig_();
  const props = scriptProperties_();
  if (props.getProperty("SERVICEIT_AUTOMATION_ENABLED") !== "YES") {
    throw new Error("SERVICEIT_AUTOMATION_DISABLED");
  }
  // Do not reserve an old tender or call GitHub when VPS handoff is OFF.
  if (props.getProperty("SERVICEIT_VPS_B2B_DIRECT_ENABLED") !== "YES") {
    return {status:"SERVICEIT_VPS_B2B_SOURCE_DISABLED",dispatched:0,crm_writes:0};
  }
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(25000)) return {status: "LOCK_BUSY"};
  try {
    assertMailbox_();
    const ids = listVendorMessageIds_().reverse();
    const pending = [];
    for (const id of ids) {
      const state = props.getProperty("SERVICEIT_EMAIL_" + id) || "";
      if (state === "BASELINE" || state === "DONE" || state === "NO_MATCH" ||
          state === "REVIEW_REQUIRED") continue;
      pending.push(id);
      if (pending.length >= SERVICEIT_CONFIG.maxMessagesPerCycle) break;
    }
    let messages = 0, dispatched = 0, skipped = 0, review = 0, uncertain = 0, deferred = 0;
    let budget = SERVICEIT_CONFIG.maxDispatchesPerCycle;
    for (const id of pending) {
      if (budget <= 0) break;
      try {
        const result = processMessage_(id, budget);
        messages++;
        dispatched += result.sent;
        skipped += result.skipped;
        uncertain += result.uncertain || 0;
        deferred += result.deferred || 0;
        budget -= result.sent + (result.uncertain || 0);
      } catch (_error) {
        props.setProperty("SERVICEIT_EMAIL_" + id, "REVIEW_REQUIRED");
        review++;
      }
    }
    const summary = {
      status: "SERVICEIT_B2B_POLL_OK",
      messages: messages,
      dispatched: dispatched,
      skipped: skipped,
      review_required: review,
      uncertain_dispatches: uncertain,
      pending_tenders_deferred: deferred,
      external_transmission: dispatched,
      crm_writes: 0
    };
    props.setProperty("SERVICEIT_LAST_RESULT", JSON.stringify(summary));
    return summary;
  } finally {
    lock.releaseLock();
  }
}

function checkB2BCenter() {
  const config = privateConfig_();
  assertMailbox_();
  const ids = listVendorMessageIds_();
  const summary = {
    status: "SERVICEIT_B2B_READONLY_PREFLIGHT",
    mailbox_verified: true,
    messages_found: ids.length,
    cutover_set: Number(config.cutoverMs) > 0,
    external_transmission: 0,
    crm_writes: 0
  };
  console.log(JSON.stringify(summary));
  return summary;
}

function activateVpsB2BCenter() {
  // One-time, owner-run from authenticated Apps Script. Requires the VPS
  // receiver HEALTH and signed end-to-end read-only preflight first.
  privateConfig_();
  assertMailbox_();
  const props = scriptProperties_();
  if (props.getProperty("SERVICEIT_VPS_B2B_DIRECT_ENABLED") === "YES") {
    return {status:"ALREADY_ACTIVATED",crm_writes:0};
  }
  const health = UrlFetchApp.fetch(
    "https://leadgen.serviseit.ru/integrations/serviceit/b2b-center/health", {
      method:"get",followRedirects:false,validateHttpsCertificates:true,
      muteHttpExceptions:true
    });
  if (!health || health.getResponseCode() !== 200) {
    throw new Error("SERVICEIT_VPS_B2B_HEALTH_UNVERIFIED");
  }
  let healthBody;
  try { healthBody = JSON.parse(health.getContentText()); }
  catch (_ignored) { throw new Error("SERVICEIT_VPS_B2B_HEALTH_FORMAT_INVALID"); }
  if (!healthBody || healthBody.status !== "B2B_INGRESS_ALIVE" ||
      healthBody.source_enabled !== true || healthBody.crm_writes !== 0) {
    throw new Error("SERVICEIT_VPS_B2B_RECEIVER_NOT_READY");
  }
  // A separate signed, read-only handshake proves the key and PostgreSQL.
  // It sends no real tender and creates no CRM card or inbox record.
  const ts = String(Math.floor(Date.now()/1000));
  const nonce = Utilities.getUuid();
  const sig = "sha256=" + hmacHex_("v1|b2b-probe|" + ts + "|" + nonce,
                                  privateConfig_().hmacSecret);
  const probe = UrlFetchApp.fetch(
    "https://leadgen.serviseit.ru/integrations/serviceit/b2b-center/probe", {
      method:"post",contentType:"application/json",
      payload:JSON.stringify({timestamp:ts,nonce:nonce,signature:sig}),
      followRedirects:false,validateHttpsCertificates:true,
      muteHttpExceptions:true
    });
  if (!probe || probe.getResponseCode() !== 200) {
    throw new Error("SERVICEIT_VPS_B2B_HMAC_OR_PG_UNVERIFIED");
  }
  let probeBody;
  try { probeBody=JSON.parse(probe.getContentText()); }
  catch (_ignored) { throw new Error("SERVICEIT_VPS_B2B_PROBE_FORMAT_INVALID"); }
  if (!probeBody || probeBody.status !== "B2B_VPS_SIGNED_PROBE_PASS" ||
      probeBody.crm_writes !== 0) {
    throw new Error("SERVICEIT_VPS_B2B_PROBE_FAILED");
  }
  // Deliberately start from the activation moment, NEVER mail backlog.
  props.setProperty("SERVICEIT_VPS_B2B_ACTIVATION_MS", String(Date.now()));
  props.setProperty("SERVICEIT_VPS_B2B_DIRECT_ENABLED", "YES");
  return installPollingTrigger();
}

function installPollingTrigger() {
  const config = privateConfig_();
  assertMailbox_();
  listVendorMessageIds_(); // bounded read-only check; no email bodies fetched.
  const props = scriptProperties_();
  Object.keys(SERVICEIT_CONFIG.knownSourceStates).forEach(function (tenderId) {
    const key = "SERVICEIT_SOURCE_" + tenderId;
    if (!props.getProperty(key)) {
      props.setProperty(key, SERVICEIT_CONFIG.knownSourceStates[tenderId]);
    }
  });
  const existing = ScriptApp.getProjectTriggers().filter(function (trigger) {
    return trigger.getHandlerFunction() === "pollAndDispatchB2B";
  });
  if (existing.length > 1) {
    throw new Error("SERVICEIT_DUPLICATE_TRIGGER_REVIEW_REQUIRED");
  }
  if (existing.length === 0) {
    ScriptApp.newTrigger("pollAndDispatchB2B")
      .timeBased().everyMinutes(SERVICEIT_CONFIG.triggerMinutes).create();
  }
  props.setProperty("SERVICEIT_AUTOMATION_ENABLED", "YES");
  const result = {
    status: "SERVICEIT_AUTOMATION_TRIGGER_INSTALLED",
    interval_minutes: SERVICEIT_CONFIG.triggerMinutes,
    gmail_identity_verified: true,
    existing_email_backfill: false,
    old_cards_touched: false,
    crm_writes_during_install: 0
  };
  props.setProperty("SERVICEIT_LAST_RESULT", JSON.stringify(result));
  console.log(JSON.stringify(result));
  return result;
}

function stopPollingTrigger() {
  const props = scriptProperties_();
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (trigger.getHandlerFunction() === "pollAndDispatchB2B") {
      ScriptApp.deleteTrigger(trigger);
    }
  });
  props.setProperty("SERVICEIT_AUTOMATION_ENABLED", "NO");
  props.setProperty("SERVICEIT_VPS_B2B_DIRECT_ENABLED", "NO");
  return {status: "SERVICEIT_AUTOMATION_STOPPED", crm_writes: 0};
}

function serviceITStatus() {
  const props = scriptProperties_();
  return {
    enabled: props.getProperty("SERVICEIT_AUTOMATION_ENABLED") === "YES",
    trigger_count: ScriptApp.getProjectTriggers().filter(function (trigger) {
      return trigger.getHandlerFunction() === "pollAndDispatchB2B";
    }).length,
    last_result: props.getProperty("SERVICEIT_LAST_RESULT") || "NOT_RUN",
    external_transmission: "ONLY_MINIMAL_TENDER_NOTICE",
    raw_email_transmitted: false,
    crm_writes_in_apps_script: 0
  };
}
