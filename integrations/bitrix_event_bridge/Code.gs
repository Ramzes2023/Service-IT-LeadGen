/**
 * ServiceIT free Bitrix24 ONCRMDYNAMICITEMADD -> GitHub Actions bridge.
 * This source is installed as a standalone, OWNER-ONLY Google Apps Script webapp.
 * It reads ONLY Bitrix's event item ID/entity type and official application_token.
 * A privately generated Secrets.gs, NOT stored in GitHub, supplies the tokens.
 *
 * No customer fields, company name, INN, phone, source URL or CRM content are
 * transmitted. Dispatched event payload contains only item ID + HMAC proof.
 *
 * Google may respond to a caller with a redirect after ContentService. The
 * SourceID and GitHub fences therefore guarantee zero blind re-dispatch.
 */
const SERVICEIT_BITRIX_BRIDGE = Object.freeze({
  repo: "Ramzes2023/Service-IT-LeadGen",
  portal: "serviseit.bitrix24.ru",
  eventType: "serviceit_bitrix_item_add_v1",
  entityTypeId: 1062,
  minItemId: 8,
  maxItemId: 2000000000,
});

function bridgeSecrets_() {
  if (typeof SERVICEIT_BITRIX_BRIDGE_PRIVATE === "undefined" ||
      !SERVICEIT_BITRIX_BRIDGE_PRIVATE ||
      SERVICEIT_BITRIX_BRIDGE_PRIVATE.ownerApproved !== "YES" ||
      SERVICEIT_BITRIX_BRIDGE_PRIVATE.enabled !== "YES" ||
      typeof SERVICEIT_BITRIX_BRIDGE_PRIVATE.outgoingApplicationToken !== "string" ||
      SERVICEIT_BITRIX_BRIDGE_PRIVATE.outgoingApplicationToken.length < 24 ||
      typeof SERVICEIT_BITRIX_BRIDGE_PRIVATE.githubToken !== "string" ||
      SERVICEIT_BITRIX_BRIDGE_PRIVATE.githubToken.length < 20 ||
      typeof SERVICEIT_BITRIX_BRIDGE_PRIVATE.hmacKey !== "string" ||
      SERVICEIT_BITRIX_BRIDGE_PRIVATE.hmacKey.length < 32) {
    throw new Error("SERVICEIT_BRIDGE_NOT_CONFIGURED");
  }
  return SERVICEIT_BITRIX_BRIDGE_PRIVATE;
}

function hex_(values) {
  return values.map(function (x) {
    return ("0" + ((x + 256) % 256).toString(16)).slice(-2);
  }).join("");
}

function sameSecret_(first, second) {
  if (typeof first !== "string" || typeof second !== "string" ||
      first.length !== second.length || first.length < 24) return false;
  const a = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,
                                    first, Utilities.Charset.UTF_8);
  const b = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,
                                    second, Utilities.Charset.UTF_8);
  let changed = 0;
  for (let i = 0; i < a.length; i++) changed |= (a[i] ^ b[i]);
  return changed === 0;
}

function oneField_(event, field) {
  const params = event && event.parameters || {};
  const value = params[field];
  if (Array.isArray(value)) {
    if (value.length !== 1) throw new Error("SERVICEIT_DUPLICATE_EVENT_FIELD");
    return String(value[0]);
  }
  if (event && event.parameter && typeof event.parameter[field] === "string") {
    return event.parameter[field];
  }
  throw new Error("SERVICEIT_REQUIRED_EVENT_FIELD_MISSING");
}

function parseBitrixEvent_(e, secret) {
  if (e && e.postData && e.postData.type &&
      e.postData.type.split(";")[0].trim().toLowerCase() === "application/json") {
    const obj = JSON.parse(String(e.postData.contents || "{}"));
    if (!obj || typeof obj !== "object") throw new Error("SERVICEIT_EVENT_JSON_INVALID");
    const auth = obj.auth || {}, data = obj.data || {}, fields = data.FIELDS || {};
    return verifyFields_(
      String(obj.event || ""), String(auth.domain || ""),
      String(auth.application_token || ""), String(fields.ID || ""),
      String(fields.ENTITY_TYPE_ID || ""), secret);
  }
  return verifyFields_(
    oneField_(e, "event"), oneField_(e, "auth[domain]"),
    oneField_(e, "auth[application_token]"),
    oneField_(e, "data[FIELDS][ID]"),
    oneField_(e, "data[FIELDS][ENTITY_TYPE_ID]"), secret);
}

function verifyFields_(event, portal, token, id, type, secret) {
  if (!sameSecret_(token, secret.outgoingApplicationToken) ||
      portal !== SERVICEIT_BITRIX_BRIDGE.portal) {
    throw new Error("SERVICEIT_OUTGOING_TOKEN_OR_PORTAL_INVALID");
  }
  const known = event.toUpperCase();
  if (known !== "ONCRMDYNAMICITEMADD" &&
      known !== "ONCRMDYNAMICITEMADD_1062") {
    return {status: "IGNORED_EVENT"};
  }
  if (!/^[1-9][0-9]{0,9}$/.test(type) ||
      Number(type) !== SERVICEIT_BITRIX_BRIDGE.entityTypeId) {
    return {status: "IGNORED_OTHER_PROCESS"};
  }
  if (!/^[1-9][0-9]{0,9}$/.test(id)) {
    throw new Error("SERVICEIT_ITEM_ID_INVALID");
  }
  const numeric = Number(id);
  if (!Number.isSafeInteger(numeric) ||
      numeric < SERVICEIT_BITRIX_BRIDGE.minItemId ||
      numeric > SERVICEIT_BITRIX_BRIDGE.maxItemId) {
    return {status: "IGNORED_HISTORICAL_OR_INVALID"};
  }
  return {status: "READY", itemId: numeric};
}

function dispatchExactId_(id, secret) {
  const when = Math.floor(Date.now() / 1000);
  const nonce = Utilities.getUuid();
  if (!/^[0-9a-f-]{32,40}$/.test(nonce)) {
    throw new Error("SERVICEIT_DISPATCH_NONCE_INVALID");
  }
  const message = "v1|" + SERVICEIT_BITRIX_BRIDGE.portal + "|1062|" +
    String(id) + "|" + String(when) + "|" + nonce;
  const signature = "sha256=" + hex_(
    Utilities.computeHmacSha256Signature(
      message, secret.hmacKey, Utilities.Charset.UTF_8));
  const event = {
    event_type: SERVICEIT_BITRIX_BRIDGE.eventType,
    client_payload: {
      version: 1,
      portal: SERVICEIT_BITRIX_BRIDGE.portal,
      entity_type_id: SERVICEIT_BITRIX_BRIDGE.entityTypeId,
      item_id: id,
      timestamp: when,
      nonce: nonce,
      signature: signature,
    },
  };
  const response = UrlFetchApp.fetch(
    "https://api.github.com/repos/" + SERVICEIT_BITRIX_BRIDGE.repo + "/dispatches", {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify(event),
      headers: {
        Authorization: "Bearer " + secret.githubToken,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      followRedirects: false,
      muteHttpExceptions: true,
      validateHttpsCertificates: true,
    });
  if (!response || response.getResponseCode() !== 204) {
    throw new Error("SERVICEIT_GITHUB_DISPATCH_UNVERIFIED");
  }
  return "DISPATCHED_ACKED";
}

function processBitrixEvent_(e) {
  const secret = bridgeSecrets_();
  const parsed = parseBitrixEvent_(e, secret);
  if (parsed.status !== "READY") return parsed.status;
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(25000)) throw new Error("SERVICEIT_BRIDGE_LOCK_BUSY");
  try {
    const key = "SERVICEIT_BITRIX_ID_" + String(parsed.itemId);
    const props = PropertiesService.getScriptProperties();
    if (props.getProperty(key)) return "DUPLICATE_NO_RETRY";
    // Reserve BEFORE external dispatch, not after. Treat any HTTP timeout
    // as UNCERTAIN, and never blindly retry or create another CRM card.
    props.setProperty(key, "RESERVED_UNVERIFIED");
    try {
      const result = dispatchExactId_(parsed.itemId, secret);
      props.setProperty(key, result);
      return result;
    } catch (_ignored) {
      props.setProperty(key, "DISPATCH_UNCERTAIN_NO_RETRY");
      throw new Error("SERVICEIT_DISPATCH_UNCERTAIN_NO_RETRY");
    }
  } finally {
    lock.releaseLock();
  }
}

function doPost(e) {
  // Always respond with a generic acknowledgement; do not reveal webhook
  // token validity, item ID, GH status or sensitive exception content.
  try { processBitrixEvent_(e); } catch (_ignored) {}
  return ContentService.createTextOutput("SERVICEIT_EVENT_RECEIVED");
}

function doGet(_e) {
  return ContentService.createTextOutput("SERVICEIT_BITRIX_BRIDGE");
}
