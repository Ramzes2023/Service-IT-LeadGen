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
 * Shared HMAC may be provided either by the existing owner-only secrets.gs
 * or, for standalone Gmail V8 scripts, by Script Properties only.
 * No GitHub credential is required by this direct-to-VPS source path.
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
  // Preserve an existing owner-only secrets.gs if it is installed.
  // Standalone Gmail V8 projects may have ONLY Code.gs, so permit a NEW,
  // scoped Script Property. Never store HMAC in committed source code.
  if (typeof SERVICEIT_PRIVATE_CONFIG !== "undefined" &&
      SERVICEIT_PRIVATE_CONFIG &&
      SERVICEIT_PRIVATE_CONFIG.ownerApproval === "YES" &&
      SERVICEIT_PRIVATE_CONFIG.expectedMailbox === SERVICEIT_CONFIG.mailbox &&
      typeof SERVICEIT_PRIVATE_CONFIG.hmacSecret === "string" &&
      SERVICEIT_PRIVATE_CONFIG.hmacSecret.length >= 32 &&
      Number.isFinite(Number(SERVICEIT_PRIVATE_CONFIG.cutoverMs)) &&
      Number(SERVICEIT_PRIVATE_CONFIG.cutoverMs) > 0) {
    return SERVICEIT_PRIVATE_CONFIG;
  }
  const props = scriptProperties_();
  const shared = props.getProperty("SERVICEIT_B2B_HMAC_SECRET") || "";
  if (props.getProperty("SERVICEIT_B2B_OWNER_APPROVAL") !== "YES" ||
      !/^[0-9a-f]{64}$/.test(shared)) {
    throw new Error("SERVICEIT_PRIVATE_CONFIG_BLOCKED");
  }
  // The real no-backfill timestamp is set by activateVpsB2BCenter().
  // Until its signed HTTPS probe passes, normal dispatch stays OFF.
  return {
    ownerApproval: "YES",
    expectedMailbox: SERVICEIT_CONFIG.mailbox,
    hmacSecret: shared,
    cutoverMs: 1
  };
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

function decodeQuotedPrintableUtf8_(encoded) {
  // Transfer-decode bytes, not JavaScript characters: B2B sends Russian
  // HTML as quoted-printable UTF-8 with soft line breaks.
  const unfolded = String(encoded).replace(/=\r?\n/g, "");
  if (unfolded.length > SERVICEIT_CONFIG.maxEmailText * 4) {
    throw new Error("SERVICEIT_EMAIL_RAW_TOO_LARGE");
  }
  const bytes = [];
  for (let i = 0; i < unfolded.length; i++) {
    const char = unfolded.charAt(i);
    if (char === "=" && /^[0-9a-f]{2}$/i.test(unfolded.slice(i + 1, i + 3))) {
      bytes.push(parseInt(unfolded.slice(i + 1, i + 3), 16));
      i += 2;
    } else {
      // Unescaped QP text is normally ASCII. Avoid one Apps Script service
      // call per HTML character, which can consume the whole polling window.
      const code = unfolded.codePointAt(i);
      if (code < 128) {
        bytes.push(code);
      } else {
        const utf8 = Utilities.newBlob(String.fromCodePoint(code)).getBytes();
        for (let j = 0; j < utf8.length; j++) {
          bytes.push((utf8[j] + 256) % 256);
        }
        if (code > 0xffff) i++;
      }
    }
  }
  return Utilities.newBlob(bytes).getDataAsString("UTF-8");
}

/**
 * Gmail Advanced Service occasionally omits .raw even though the owner is
 * authorized. Make a second READ-ONLY request to the SAME Gmail API via
 * UrlFetchApp with the Apps Script OAuth token. No MIME leaves Google.
 */
function readRawGmailDirect_(messageId) {
  if (!/^[a-f0-9]{16,24}$/i.test(String(messageId || ""))) {
    throw new Error("SERVICEIT_EMAIL_RAW_MESSAGE_ID_INVALID");
  }
  const response = UrlFetchApp.fetch(
    "https://gmail.googleapis.com/gmail/v1/users/me/messages/" +
      encodeURIComponent(messageId) + "?format=raw", {
      method: "get",
      headers: {Authorization: "Bearer " + ScriptApp.getOAuthToken()},
      followRedirects: false,
      validateHttpsCertificates: true,
      muteHttpExceptions: true
    }
  );
  const code = response && response.getResponseCode();
  if (code !== 200) {
    const safe = Number.isInteger(code) && code >= 400 && code <= 599 ?
      String(code) : "UNKNOWN";
    throw new Error("SERVICEIT_EMAIL_RAW_REST_HTTP_" + safe);
  }
  let result;
  try { result = JSON.parse(response.getContentText()); }
  catch (_ignored) { throw new Error("SERVICEIT_EMAIL_RAW_REST_INVALID_JSON"); }
  if (!result || result.id !== messageId || typeof result.raw !== "string" ||
      !result.raw || result.raw.length > SERVICEIT_CONFIG.maxEmailText * 8) {
    throw new Error("SERVICEIT_EMAIL_RAW_REST_UNAVAILABLE");
  }
  return result.raw;
}

function messageTextFromRaw_(message) {
  // Gmail may return an empty MessagePart.body.data even for genuine
  // authenticated single-part B2B HTML. Use Gmail's READ-ONLY raw response
  // for those messages; raw MIME stays inside Apps Script and is NEVER
  // transmitted to VPS, GitHub or CRM.
  if (!message || !/^[a-f0-9]{16,24}$/i.test(String(message.id || ""))) {
    throw new Error("SERVICEIT_EMAIL_RAW_MESSAGE_ID_INVALID");
  }
  let raw = null;
  try {
    raw = Gmail.Users.Messages.get("me", message.id, {format: "raw"});
  } catch (_ignored) {
    // A fallback to the same authenticated Gmail endpoint is still read-only.
  }
  const rawValue = raw && raw.id === message.id &&
    typeof raw.raw === "string" && raw.raw &&
    raw.raw.length <= SERVICEIT_CONFIG.maxEmailText * 8 ?
      raw.raw : readRawGmailDirect_(message.id);
  const mime = Utilities.newBlob(
    Utilities.base64DecodeWebSafe(rawValue)).getDataAsString("UTF-8");
  if (mime.length > SERVICEIT_CONFIG.maxEmailText * 4) {
    throw new Error("SERVICEIT_EMAIL_RAW_TOO_LARGE");
  }
  const separator = /\r?\n\r?\n/.exec(mime);
  if (!separator) throw new Error("SERVICEIT_EMAIL_MIME_INVALID");
  const headers = mime.slice(0, separator.index).replace(/\r?\n[ \t]+/g, " ");
  const body = mime.slice(separator.index + separator[0].length);
  const contentType = (headers.match(/^Content-Type:\s*([^\r\n]+)/mi) || [])[1] || "";
  const encoding = (headers.match(/^Content-Transfer-Encoding:\s*([^\r\n]+)/mi) || [])[1] || "";
  // Fail closed on complex multipart payloads, attachments and unexpected
  // MIME encodings. Only direct text/plain or text/html is supported here.
  if (!/^text\/(?:plain|html)(?:\s*;|\s*$)/i.test(contentType)) {
    throw new Error("SERVICEIT_EMAIL_RAW_MIME_UNSUPPORTED");
  }
  let value;
  if (/^quoted-printable$/i.test(encoding.trim())) {
    value = decodeQuotedPrintableUtf8_(body);
  } else if (/^base64$/i.test(encoding.trim())) {
    value = Utilities.newBlob(Utilities.base64Decode(body.replace(/\s+/g, "")))
      .getDataAsString("UTF-8");
  } else if (!encoding || /^(?:7bit|8bit)$/i.test(encoding.trim())) {
    value = body;
  } else {
    throw new Error("SERVICEIT_EMAIL_RAW_ENCODING_UNSUPPORTED");
  }
  const normalized = /^text\/html/i.test(contentType) ? htmlToText_(value) : value;
  if (!normalized || normalized.length > SERVICEIT_CONFIG.maxEmailText) {
    throw new Error("SERVICEIT_EMAIL_CONTENT_INVALID");
  }
  return normalized;
}

function messageText_(message) {
  const output = {plain: [], html: []};
  collectParts_(message.payload || {}, output);
  const candidates = output.plain.concat(output.html.map(htmlToText_));
  const text = candidates.sort(function (a, b) {
    return (b.match(/b2b-center\.ru/gi) || []).length -
      (a.match(/b2b-center\.ru/gi) || []).length;
  })[0] || "";
  if (!text) return messageTextFromRaw_(message);
  if (text.length > SERVICEIT_CONFIG.maxEmailText) {
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
    // Only an explicit tender to provide IT personnel qualifies; a vacancy,
    // general recruitment or a generic outsourcing headline is not enough.
    "IT_STAFFING": new RegExp(
      "(?:предоставлени|привлечени|аутстаффинг)" + suffix +
      "\\s+(?:ит|it)[\\s-]*специалист" + suffix,
      "iu"
    ),
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
    ),
    "CLOUD": new RegExp(
      "облачн" + suffix + "\\s+(?:сервис" + suffix + "|инфраструктур" + suffix +
      "|платформ" + suffix + "|хранилищ" + suffix + "|решени" + suffix +
      ")|(?:основн" + suffix + "|резервн" + suffix + ")\\s+облак" + suffix +
      "|(?<!" + WORD + ")(?:iaas|paas|saas)(?!" + WORD + ")", "iu"
    ),
    "LICENSES": new RegExp(
      "(?:приобретени" + suffix + "|закупк" + suffix +
      "|продлени" + suffix + "|поставк" + suffix + ")\\s+" +
      "(?:программн" + suffix + "\\s+)?лицензи" + suffix +
      "\\s*(?:microsoft\\s*)?(?:teams|office\\s*365|m365|microsoft\\s*365|windows|exchange|sharepoint)" +
      "|лицензи" + suffix + "\\s+(?:на\\s+)?(?:программн" + suffix +
      "\\s+обеспечен" + suffix + "|по)" +
      "|(?:продлени" + suffix + "|поставк" + suffix +
      ")\\s+(?:лицензи" + suffix + "\\s+)?(?:microsoft\\s*)?(?:teams|m365|office\\s*365)", "iu"
    )
  };
  const result = [];
  Object.keys(patterns).forEach(function (key) {
    if (patterns[key].test(title)) result.push(key);
  });
  // Source-backed software-service procurements, not generic consultant jobs.
  // Covers both "КонсультантПлюс" and "Консультант Плюс" with a procurement
  // action; keep the existing LICENSES contract for the VPS coordinator.
  const consultantPlusLicense = new RegExp(
    "(?:поставк|сопровождени|адаптаци|продлени|предоставлени)" + suffix +
    ".{0,180}консультант\\s*плюс" +
    "|консультант\\s*плюс.{0,100}(?:сопровождени|адаптаци|поставк|продлени|лицензи)" + suffix,
    "iu"
  );
  // B2B's 13 Nov 2026 digest 4681383 uses a product-only catalogue name.
  // Keep this exact branded database title; avoid generic articles/jobs.
  const consultantPlusCatalog = /^\s*консультант\s*плюс\s*\(\s*справочно[- ]правовые\s+базы\s+данных\s*\)\s*\.?\s*$/iu;
  if ((consultantPlusLicense.test(title) || consultantPlusCatalog.test(title)) &&
      result.indexOf("LICENSES") < 0) {
    result.push("LICENSES");
  }
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
  // A closing HTML tag can leave a space before the colon in invitations.
  const hasInvite = /Организатор\s+закупки\s*:/i.test(text) &&
    /Предмет закупки\s*:/i.test(text);
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
  // The old implementation accepted any HTTP 200/202 (including a reverse
  // proxy HTML page) and permanently marked the email DISPATCHED even though
  // the VPS may never have stored it. Require the exact source inbox receipt.
  let receipt;
  try {
    receipt = JSON.parse(response.getContentText());
  } catch (_error) {
    throw new Error("SERVICEIT_VPS_NOTICE_RECEIPT_NOT_JSON");
  }
  const expected = response.getResponseCode() === 202 ?
    "B2B_NOTICE_PERSISTED" : "B2B_NOTICE_DUPLICATE";
  if (!receipt || receipt.status !== expected || receipt.crm_writes !== 0) {
    throw new Error("SERVICEIT_VPS_NOTICE_RECEIPT_UNVERIFIED");
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

/**
 * A parser/classifier update may find IT tenders in a previously DONE digest:
 * an email with 3 tenders might have sent 1 and silently ignored 2.
 * Re-evaluate up to four DONE/NO_MATCH emails per cycle, ONCE PER REVISION.
 * Only post-activation authenticated notices with NO source receipt may be
 * marked PENDING. Nothing can undo a source reservation or security review.
 */
function recoverPostCutoverMissedV6_(ids) {
  const props = scriptProperties_();
  const activation = Math.max(Number(privateConfig_().cutoverMs),
    Number(props.getProperty("SERVICEIT_VPS_B2B_ACTIVATION_MS") || 0));
  const revision = "B2B_CLASSIFIER_V6";
  let checked = 0, queued = 0;
  for (const id of ids) {
    if (checked >= 4) break;
    const key = "SERVICEIT_EMAIL_" + id;
    if (["NO_MATCH", "DONE"].indexOf(props.getProperty(key)) < 0) continue;
    const probeKey = "SERVICEIT_" + revision + "_" + id;
    if (props.getProperty(probeKey)) continue;
    checked++;
    try {
      const message = Gmail.Users.Messages.get("me", id, {format: "full"});
      if (!message || message.id !== id) continue;
      const parsed = parseEmailMessage_(message, id);
      if (parsed.internalDate <= activation) {
        props.setProperty(probeKey, "BEFORE_CUTOVER");
        continue;
      }
      const candidates = parsed.observations.filter(function (row) {
        return !props.getProperty("SERVICEIT_SOURCE_" + row.notice.tender_id);
      });
      if (candidates.length) {
        props.setProperty(key, "PENDING");
        queued++;
      }
      props.setProperty(probeKey, candidates.length ? "QUEUED" : "NO_NEW_SOURCE");
    } catch (_err) {
      // Do not modify DONE/NO_MATCH on parse/transport failure. Do not
      // automatically release security quarantines (REVIEW_REQUIRED).
    }
  }
  return {checked: checked, queued: queued};
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
    const recovered = recoverPostCutoverMissedV6_(ids);
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
      last_poll_utc: new Date().toISOString(),
      vendor_messages_visible: ids.length,
      pending_emails_scanned: pending.length,
      messages: messages,
      dispatched: dispatched,
      skipped: skipped,
      review_required: review,
      uncertain_dispatches: uncertain,
      pending_tenders_deferred: deferred,
      recovered_missed_messages: recovered.queued,
      missed_messages_rechecked: recovered.checked,
      external_transmission: dispatched,
      crm_writes: 0
    };
    props.setProperty("SERVICEIT_LAST_RESULT", JSON.stringify(summary));
    return summary;
  } finally {
    lock.releaseLock();
  }
}

/**
 * Owner-run read-only status of the LIVE Apps Script trigger and email states.
 * Never prints subject, MIME, message ID, secret, or customer data.
 */
/**
 * Owner-only read-only trace of FOUR independently observed vendor tender IDs.
 * This reveals only fixed state names and counts, never Gmail message IDs,
 * subject text, HMAC secret, dispatch UUID, source payload or contacts.
 * Source markers cannot be reset by this function.
 */
function serviceITB2BExactDeliveryAuditReadOnly() {
  privateConfig_();
  assertMailbox_();
  const props = scriptProperties_();
  const tenderIds = ["4677923", "4677837", "4674919", "4679263"];
  const safeStates = ["DONE","NO_MATCH","BASELINE","REVIEW_REQUIRED",
    "PARTIAL_REVIEW","PENDING","UNPROCESSED"];
  const safeSource = /^(DISPATCHED|DISPATCH_UNCERTAIN|RESERVED|RETIRED_DO_NOT_RECREATE|EXISTS_DO_NOT_RECREATE)(?::|$)/;
  const rows = tenderIds.map(function (tid) {
    const receipt = props.getProperty("SERVICEIT_SOURCE_" + tid) || "";
    const statusMatch = receipt.match(safeSource);
    const row = {
      tender_id: tid,
      source_status: statusMatch ? statusMatch[1] : receipt ? "OTHER_REVIEW" : "NO_SOURCE_RECEIPT",
      vendor_emails: 0,
      email_states: {}
    };
    // Search only exact tender number and authenticated vendor address;
    // no email is opened, modified or transmitted to our VPS.
    const response = Gmail.Users.Messages.list("me", {
      q: "from:marketplace@b2b-center.ru newer_than:14d \"" + tid + "\"",
      maxResults: 10,
      includeSpamTrash: false
    });
    const matches = response && Array.isArray(response.messages) ? response.messages : [];
    if (response && response.nextPageToken) {
      row.email_search = "MORE_THAN_10_REVIEW";
    }
    matches.forEach(function (item) {
      if (!item || typeof item.id !== "string" ||
          !/^[a-f0-9]{16,24}$/i.test(item.id)) {
        throw new Error("SERVICEIT_GMAIL_MESSAGE_ID_INVALID");
      }
      row.vendor_emails++;
      const state = props.getProperty("SERVICEIT_EMAIL_" + item.id) || "UNPROCESSED";
      const safe = safeStates.indexOf(state) >= 0 ? state : "OTHER_REVIEW";
      row.email_states[safe] = (row.email_states[safe] || 0) + 1;
    });
    return row;
  });
  const result = {
    status: "SERVICEIT_B2B_EXACT_MISSING_TENDERS_READONLY",
    tenders: rows,
    vps_posts: 0,
    crm_writes: 0,
    source_properties_modified: 0
  };
  console.log(JSON.stringify(result));
  return result;
}

function serviceITB2BDeliveryStatusReadOnly() {
  privateConfig_();
  assertMailbox_();
  const props = scriptProperties_();
  const ids = listVendorMessageIds_();
  const states = {};
  ["DONE","NO_MATCH","BASELINE","REVIEW_REQUIRED","PARTIAL_REVIEW",
   "PENDING"].forEach(function (name) { states[name] = 0; });
  ids.forEach(function (id) {
    const state = props.getProperty("SERVICEIT_EMAIL_" + id) || "UNPROCESSED";
    states[state] = (states[state] || 0) + 1;
  });
  const triggerCount = ScriptApp.getProjectTriggers().filter(function (trigger) {
    return trigger.getHandlerFunction() === "pollAndDispatchB2B";
  }).length;
  const last = props.getProperty("SERVICEIT_LAST_RESULT") || "";
  let lastResult = {};
  try {
    const parsed = JSON.parse(last);
    ["status","last_poll_utc","vendor_messages_visible",
     "pending_emails_scanned","messages","dispatched","review_required",
     "uncertain_dispatches","pending_tenders_deferred",
     "recovered_missed_messages","missed_messages_rechecked"].forEach(function (key) {
      if (typeof parsed[key] === "number" || typeof parsed[key] === "string") {
        lastResult[key] = parsed[key];
      }
    });
  } catch (_e) {}
  const result = {
    status: "SERVICEIT_B2B_LIVE_DELIVERY_READONLY",
    automation_enabled: props.getProperty("SERVICEIT_AUTOMATION_ENABLED") === "YES",
    vps_direct_enabled: props.getProperty("SERVICEIT_VPS_B2B_DIRECT_ENABLED") === "YES",
    trigger_count: triggerCount,
    messages_sampled: ids.length,
    states: states,
    last_result: lastResult,
    crm_writes: 0,
    vps_posts: 0
  };
  console.log(JSON.stringify(result));
  return result;
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


/**
 * Owner-run ONLY: inspect most recent quarantined B2B emails, READ-ONLY.
 * Exposes fixed processing codes and counts; no raw email data, Gmail IDs,
 * subjects, sender addresses, HMAC secrets, inbox mutations or VPS POSTs.
 * REVIEW_REQUIRED and source dedup state remain unchanged.
 */
/**
 * One-time, owner-run recovery ONLY for the two verified Nov 10 B2B vendor
 * bulletins. This does NOT POST, write CRM, touch unrelated emails, or replay
 * any source whose dispatch receipt already exists.
 * After validation, regular five-minute polling delivers PENDING naturally.
 */
function recoverNov10B2BCenterAfterFix() {
  privateConfig_();
  assertMailbox_();
  const props = scriptProperties_();
  if (props.getProperty("SERVICEIT_AUTOMATION_ENABLED") !== "YES" ||
      props.getProperty("SERVICEIT_VPS_B2B_DIRECT_ENABLED") !== "YES") {
    throw new Error("SERVICEIT_B2B_RECOVERY_AUTOMATION_NOT_ACTIVE");
  }
  const activation = Number(props.getProperty("SERVICEIT_VPS_B2B_ACTIVATION_MS") || 0);
  if (!Number.isFinite(activation) || activation <= 0) {
    throw new Error("SERVICEIT_B2B_RECOVERY_ACTIVATION_MISSING");
  }
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(25000)) throw new Error("SERVICEIT_B2B_RECOVERY_LOCK_BUSY");
  try {
    // From the owner's actual Gmail Nov 10 10:07 MSK search-template and
    // profile digests. No other historical or later Gmail ID is eligible.
    const targets = ["1a1c44a89c79ccd6", "1a1c449c537d5613"];
    const prepared = [];
    let recognized = 0;
    for (const id of targets) {
      const state = props.getProperty("SERVICEIT_EMAIL_" + id) || "";
      if (state !== "REVIEW_REQUIRED") continue;
      const message = Gmail.Users.Messages.get("me", id, {format: "full"});
      if (!message || message.id !== id) {
        throw new Error("SERVICEIT_B2B_RECOVERY_GMAIL_MISMATCH");
      }
      const parsed = parseEmailMessage_(message, id);
      if (parsed.internalDate <= activation || !parsed.observations.length) {
        throw new Error("SERVICEIT_B2B_RECOVERY_INELIGIBLE");
      }
      for (const row of parsed.observations) {
        if (props.getProperty("SERVICEIT_SOURCE_" + row.notice.tender_id)) {
          throw new Error("SERVICEIT_B2B_RECOVERY_EXISTING_RECEIPT");
        }
      }
      recognized += parsed.observations.length;
      prepared.push(id);
    }
    // Only now release the *two exact reviewed* Gmail markers. New dispatch
    // is handled independently by the existing recurring trigger.
    prepared.forEach(function (id) {
      props.setProperty("SERVICEIT_EMAIL_" + id, "PENDING");
    });
    const result = {
      status: "SERVICEIT_B2B_EXACT_RECOVERY_QUEUED",
      messages_released: prepared.length,
      observed_it_tenders: recognized,
      vps_posts_during_recovery: 0,
      crm_writes_during_recovery: 0
    };
    console.log(JSON.stringify(result));
    return result;
  } finally {
    lock.releaseLock();
  }
}

/**
 * OPTIONAL owner-run exact resend of a single IT-staffing tender #4673595.
 * This cannot run through a timer, and cannot resend any recorded source.
 * Requires the VPS to have the same IT_STAFFING classifier deployed first.
 */
function dispatchB2B4673595Once() {
  privateConfig_();
  assertMailbox_();
  const props = scriptProperties_();
  if (props.getProperty("SERVICEIT_AUTOMATION_ENABLED") !== "YES" ||
      props.getProperty("SERVICEIT_VPS_B2B_DIRECT_ENABLED") !== "YES") {
    throw new Error("SERVICEIT_B2B_STAFFING_SOURCE_NOT_ACTIVE");
  }
  const activation = Number(props.getProperty("SERVICEIT_VPS_B2B_ACTIVATION_MS") || 0);
  if (!Number.isFinite(activation) || activation <= 0) {
    throw new Error("SERVICEIT_B2B_STAFFING_ACTIVATION_MISSING");
  }
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(25000)) throw new Error("SERVICEIT_B2B_STAFFING_LOCK_BUSY");
  try {
    const id = "1a1c44a89c79ccd6";
    const tenderId = "4673595";
    const sourceKey = "SERVICEIT_SOURCE_" + tenderId;
    if (props.getProperty(sourceKey)) {
      const existing = {
        status: "SERVICEIT_B2B_STAFFING_ALREADY_RESERVED_OR_HANDLED",
        tender_id: tenderId, vps_posts: 0, crm_writes: 0
      };
      console.log(JSON.stringify(existing));
      return existing;
    }
    const msg = Gmail.Users.Messages.get("me", id, {format: "full"});
    if (!msg || msg.id !== id) {
      throw new Error("SERVICEIT_B2B_STAFFING_EMAIL_MISMATCH");
    }
    const parsed = parseEmailMessage_(msg, id);
    if (parsed.internalDate <= activation) {
      throw new Error("SERVICEIT_B2B_STAFFING_BEFORE_ACTIVATION");
    }
    const eligible = parsed.observations.filter(function (row) {
      return row.notice.tender_id === tenderId &&
        row.services.indexOf("IT_STAFFING") >= 0;
    });
    if (eligible.length !== 1) {
      throw new Error("SERVICEIT_B2B_STAFFING_TENDER_NOT_VERIFIED");
    }
    const dispatchId = Utilities.getUuid();
    props.setProperty(sourceKey, "RESERVED:" + dispatchId);
    try {
      dispatchNotice_(eligible[0].notice, dispatchId);
    } catch (_error) {
      props.setProperty(sourceKey, "DISPATCH_UNCERTAIN:" + dispatchId);
      const uncertain = {
        status: "SERVICEIT_B2B_STAFFING_DISPATCH_UNCERTAIN_NO_RETRY",
        tender_id: tenderId, crm_writes: 0
      };
      console.log(JSON.stringify(uncertain));
      return uncertain;
    }
    props.setProperty(sourceKey, "DISPATCHED:" + dispatchId);
    const sent = {
      status: "SERVICEIT_B2B_STAFFING_NOTICE_ACCEPTED",
      tender_id: tenderId, vps_notices_accepted: 1, crm_writes: 0
    };
    console.log(JSON.stringify(sent));
    return sent;
  } finally {
    lock.releaseLock();
  }
}

/** Queue only the missed, authenticated server-support invitation for normal polling. */
function recoverB2B4677837AfterParserFix() {
  const config = privateConfig_();
  assertMailbox_();
  const props = scriptProperties_();
  if (props.getProperty("SERVICEIT_AUTOMATION_ENABLED") !== "YES" ||
      props.getProperty("SERVICEIT_VPS_B2B_DIRECT_ENABLED") !== "YES") {
    throw new Error("SERVICEIT_B2B_RECOVERY_AUTOMATION_NOT_ACTIVE");
  }
  const cutover = Math.max(Number(config.cutoverMs),
    Number(props.getProperty("SERVICEIT_VPS_B2B_ACTIVATION_MS") || 0));
  if (!Number.isFinite(cutover) || cutover <= 0) {
    throw new Error("SERVICEIT_B2B_RECOVERY_ACTIVATION_MISSING");
  }
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(25000)) throw new Error("SERVICEIT_B2B_RECOVERY_LOCK_BUSY");
  try {
    const tender = "4677837";
    if (props.getProperty("SERVICEIT_SOURCE_" + tender)) {
      return {status: "SERVICEIT_B2B_RECOVERY_ALREADY_HANDLED"};
    }
    for (const id of listVendorMessageIds_().slice(0, 20)) {
      const key = "SERVICEIT_EMAIL_" + id;
      if (props.getProperty(key) !== "NO_MATCH") continue;
      const message = Gmail.Users.Messages.get("me", id, {format: "full"});
      if (!message || message.id !== id) continue;
      let parsed;
      try {
        parsed = parseEmailMessage_(message, id);
      } catch (_ignored) {
        continue;
      }
      if (parsed.internalDate > cutover && parsed.observations.length === 1 &&
          parsed.observations[0].notice.tender_id === tender) {
        props.setProperty(key, "PENDING");
        return {status: "SERVICEIT_B2B_EXACT_RECOVERY_QUEUED", tender_id: tender};
      }
    }
    return {status: "SERVICEIT_B2B_RECOVERY_NO_ELIGIBLE_MAIL"};
  } finally {
    lock.releaseLock();
  }
}

function diagnoseB2BCenterReadOnly() {
  assertMailbox_();
  const props = scriptProperties_();
  const ids = listVendorMessageIds_();
  const failed = ids.filter(function (id) {
    return props.getProperty("SERVICEIT_EMAIL_" + id) === "REVIEW_REQUIRED";
  });
  const result = {
    status: "SERVICEIT_B2B_REVIEW_DIAGNOSTIC",
    flagged_messages: failed.length,
    inspected_messages: 0,
    samples: [],
    source_states_changed: 0,
    gmail_modified: false,
    vps_posts: 0,
    crm_writes: 0
  };
  failed.slice(0, 6).forEach(function (id, index) {
    const sample = {slot: index + 1, phase: "GMAIL_GET"};
    try {
      const message = Gmail.Users.Messages.get("me", id, {format: "full"});
      if (!message || message.id !== id) {
        throw new Error("SERVICEIT_GMAIL_READBACK_ID_MISMATCH");
      }
      sample.phase = "PARSE";
      const parsed = parseEmailMessage_(message, id);
      sample.phase = "PARSED";
      sample.recognized_tenders = parsed.observations.length;
      sample.post_activation = parsed.internalDate >
        Number(props.getProperty("SERVICEIT_VPS_B2B_ACTIVATION_MS") || 0);
    } catch (err) {
      const message = String(err && err.message || "");
      sample.code = /^SERVICEIT_[A-Z0-9_]{3,100}$/.test(message) ?
        message : sample.phase === "GMAIL_GET" ?
          "SERVICEIT_GMAIL_GET_FAILED" : "SERVICEIT_PARSE_UNCLASSIFIED";
    }
    result.samples.push(sample);
  });
  result.inspected_messages = result.samples.length;
  console.log(JSON.stringify(result));
  return result;
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
