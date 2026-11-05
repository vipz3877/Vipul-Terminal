/**
 * ☸ VIPUL QUANTUM TERMINAL — PRODUCTION MASTER SCRIPT (v3.6 - Clean & Secure)
 * Audited 3-Pillar Execution | Dynamic KP + Vedic + Gann Price-Time Windows
 * Range-Bound & Chop Aware | 10-Day Forward Matrix | Hardened Anti-Spam Firewall
 */

// --- GLOBAL CONFIGURATION (CREDENTIALS SECURED VIA PROPERTIES SERVICE) ---
function GEMINI_API_KEY_() { return PropertiesService.getScriptProperties().getProperty("GEMINI_API_KEY") || ""; }

var MUMBAI_TZ = "Asia/Kolkata";
var MUMBAI_LAT = 19.0760;
var MUMBAI_LNG = 72.8777;

var SHEET_ASTRO = "⚡ ASTRO TERMINAL";
var SHEET_GANN = "📐 GANN LEVELS";
var SHEET_JOURNAL = "📖 TRADE JOURNAL";
var SHEET_LEARN = "🧠 AI SELF-LEARNING ENGINE";
var GQ_ = "'" + SHEET_GANN + "'!";

var STOP_POINTS = 30;      // stop-loss distance in index points
var RISK_PCT = 0.02;       // 2% of capital per trade
var AUTO_TG_COOLDOWN_MS = 15 * 60 * 1000; // Hard 15-min minimum cooldown between auto Telegram alerts
var LOT_SIZES = { "NIFTY 50": 65, "BANK NIFTY": 30, "FINNIFTY": 60 };

var NSE_HOLIDAYS_2026_ = {
  "2026-01-15": "Municipal Corporation Election", "2026-01-26": "Republic Day",
  "2026-03-03": "Holi", "2026-03-26": "Shri Ram Navami", "2026-03-31": "Mahavir Jayanti",
  "2026-04-03": "Good Friday", "2026-04-14": "Ambedkar Jayanti", "2026-05-01": "Maharashtra Day",
  "2026-05-28": "Bakri Id", "2026-06-26": "Muharram", "2026-09-14": "Ganesh Chaturthi",
  "2026-10-02": "Gandhi Jayanti", "2026-10-20": "Dussehra", "2026-11-10": "Diwali-Balipratipada",
  "2026-11-24": "Guru Nanak Jayanti", "2026-12-25": "Christmas"
};

var NAKSHATRA_SIMPLE_ = {
  "Ashwini": { lord: "Ketu", style: "Fast Morning Breakout", advice: "Strong 1st-hour speed; trade breakouts above Buy Level with tight stop." },
  "Bharani": { lord: "Venus", style: "Stop-Hunt & Reversal", advice: "Watch for fakeouts at Resistance/Support walls; fade failed tests." },
  "Krittika": { lord: "Sun", style: "Sharp Directional Trend", advice: "Strong trend day after 10:15 AM; ride momentum in trend direction." },
  "Rohini": { lord: "Moon", style: "Steady Accumulation", advice: "Institutional buying on dips; buy near Support 1 or 50% Pivot." },
  "Mrigashira": { lord: "Mars", style: "Two-Way Swing", advice: "Tests both sides early; wait for 2-bar confirmation before entering." },
  "Ardra": { lord: "Rahu", style: "High Volatility / Trap", advice: "Sharp whipsaws common; cut lot size in half and wait for wall test." },
  "Punarvasu": { lord: "Jupiter", style: "V-Bounce Recovery", advice: "High probability of strong recovery bounces from Support walls." },
  "Pushya": { lord: "Saturn", style: "Steady Institutional Trend", advice: "Smooth, disciplined trend; Banking & PSU stocks lead." },
  "Ashlesha": { lord: "Mercury", style: "Sudden Unwind / Whipsaw", advice: "Coils tightly then spikes; book profits quickly at Target 1." },
  "Magha": { lord: "Ketu", style: "Big Player Dominance", advice: "Index heavyweights drive the move; respect major Support/Resistance." },
  "Purva Phalguni": { lord: "Venus", style: "Morning Push / Midday Range", advice: "Capture the morning move before 11:30 AM; trail stops tightly." },
  "Uttara Phalguni": { lord: "Sun", style: "Sustained Trend Day", advice: "Trends hold firmly into the afternoon session." },
  "Hasta": { lord: "Moon", style: "Clean Technical Scalping", advice: "Respects Gann 45° Support & Resistance with textbook precision." },
  "Chitra": { lord: "Mars", style: "Pinpoint Gann Geometry", advice: "Clean breakouts and bounces off exact Gann levels." },
  "Swati": { lord: "Rahu", style: "Wide Range Swings", advice: "Wide intraday candles; do not chase extended moves in mid-range." },
  "Vishakha": { lord: "Jupiter", style: "Target Completion", advice: "Strong magnet pull toward Target 1 and Target 2 once triggered." },
  "Anuradha": { lord: "Saturn", style: "Orderly Channel", advice: "Slow morning consolidation followed by steady directional move." },
  "Jyeshtha": { lord: "Mercury", style: "Peak & Reversal", advice: "Watch for trend exhaustion and sharp 2-bar reversals at Resistance 2 / Support 2." },
  "Mula": { lord: "Ketu", style: "Sharp Structure Break", advice: "Breaks old ranges violently; trade strictly with 2-bar confirmation." },
  "Purva Ashadha": { lord: "Venus", style: "Momentum Follow-Through", advice: "Breakouts above Resistance 1 or below Support 1 see strong follow-through." },
  "Uttara Ashadha": { lord: "Sun", style: "High-Conviction Trend", advice: "Strong institutional flow; hold runners for Target 2." },
  "Shravana": { lord: "Moon", style: "Volume & Flow Driven", advice: "Moves accelerate at key reversal times; follow the higher timeframe trend." },
  "Dhanishta": { lord: "Mars", style: "Breakout Volume Surge", advice: "Aggressive momentum spikes; great for option buying on breakouts." },
  "Shatabhisha": { lord: "Rahu", style: "Fakeout Then Reversal", advice: "Sweeps stops beyond Support/Resistance then reverses sharply." },
  "Purva Bhadrapada": { lord: "Jupiter", style: "Wide Expansive Moves", advice: "High velocity; follow confirmed 2-bar breakouts or breakdowns." },
  "Uttara Bhadrapada": { lord: "Saturn", style: "Strong Base Building", advice: "Holds support firmly and accelerates in the second half." },
  "Revati": { lord: "Mercury", style: "Late-Session Profit Booking", advice: "Watch for trend reversal or profit booking after 01:45 PM." }
};

// ===================== CREDENTIALS HELPER =====================
function CLEAN_(v) {
  v = String(v === undefined || v === null ? "" : v).trim();
  return (!v || v.indexOf("PASTE_") === 0) ? "" : v;
}

function CREDS_() {
  var p = PropertiesService.getScriptProperties();
  return {
    client: CLEAN_(p.getProperty("DHAN_CLIENT_ID")),
    token: CLEAN_(p.getProperty("DHAN_ACCESS_TOKEN")),
    tgToken: CLEAN_(p.getProperty("TELEGRAM_BOT_TOKEN")),
    tgChat: CLEAN_(p.getProperty("TELEGRAM_CHAT_ID"))
  };
}

function PROMPT_(ui, title, msg) {
  var r = ui.prompt(title, msg, ui.ButtonSet.OK_CANCEL);
  if (r.getSelectedButton() !== ui.Button.OK) return null;
  return r.getResponseText().replace(/[\s"']/g, "");
}

function SET_CREDENTIALS_UI() {
  var ui = SpreadsheetApp.getUi();
  var props = PropertiesService.getScriptProperties();
  var fields = [
    ["DHAN_CLIENT_ID", "Dhan Client ID"],
    ["DHAN_ACCESS_TOKEN", "Dhan Access Token (expires every 24 hours)"],
    ["TELEGRAM_BOT_TOKEN", "Telegram Bot Token (from BotFather)"],
    ["TELEGRAM_CHAT_ID", "Telegram Chat ID (e.g. -100xxxxxxxxxx)"],
    ["GEMINI_API_KEY", "Gemini API Key"]
  ];
  var saved = [];
  for (var i = 0; i < fields.length; i++) {
    var cur = props.getProperty(fields[i][0]);
    var v = PROMPT_(ui, fields[i][1], "Paste the NEW value. Leave blank to keep current (" + (cur ? "override set" : "not set") + ").");
    if (v === null) break;
    if (v) { props.setProperty(fields[i][0], v); saved.push(fields[i][1]); }
  }
  CLEAR_CACHE_();
  ui.alert(saved.length ? "✅ Saved:\n• " + saved.join("\n• ") : "No changes made.");
}

function UPDATE_DHAN_TOKEN_UI() {
  var ui = SpreadsheetApp.getUi();
  var v = PROMPT_(ui, "Dhan Access Token", "Paste today's new Dhan access token:");
  if (!v) return;
  PropertiesService.getScriptProperties().setProperty("DHAN_ACCESS_TOKEN", v);
  CLEAR_CACHE_();
  ui.alert("✅ Dhan token updated.");
}

function CLEAR_CACHE_() {
  try { CacheService.getScriptCache().removeAll(["ltp_13", "ltp_25", "ltp_27", "bars_13", "bars_25", "bars_27"]); } catch (e) {}
}

// ===================== CALENDAR HELPERS =====================
function SHEET_TZ_() {
  try { return SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone() || MUMBAI_TZ; } catch (e) { return MUMBAI_TZ; }
}

function DATE_PARTS_(input) {
  var d = null, tz = MUMBAI_TZ, m;
  if (input instanceof Date && !isNaN(input.getTime())) {
    d = input; tz = SHEET_TZ_();
  } else if (typeof input === "number" && isFinite(input)) {
    var u = new Date(Math.round((input - 25569) * 86400000));
    return { y: u.getUTCFullYear(), mo: u.getUTCMonth() + 1, d: u.getUTCDate() };
  } else if (input !== undefined && input !== null && String(input).trim() !== "") {
    var s = String(input).trim();
    m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
    if (m) return { y: parseInt(m[1], 10), mo: parseInt(m[2], 10), d: parseInt(m[3], 10) };
    m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (m) return { y: parseInt(m[3], 10), mo: parseInt(m[1], 10), d: parseInt(m[2], 10) };
    var t = new Date(s);
    if (!isNaN(t.getTime())) { d = t; tz = SHEET_TZ_(); }
  }
  if (!d) { d = new Date(); tz = MUMBAI_TZ; }
  var p = Utilities.formatDate(d, tz, "yyyy-MM-dd").split("-");
  return { y: parseInt(p[0], 10), mo: parseInt(p[1], 10), d: parseInt(p[2], 10) };
}

function IS_NSE_TRADING_DAY_(d) {
  var dt = d ? new Date(d.getTime()) : new Date();
  var dayNum = parseInt(Utilities.formatDate(dt, MUMBAI_TZ, "u"), 10);
  if (dayNum === 6 || dayNum === 7) return false;
  return !NSE_HOLIDAYS_2026_[Utilities.formatDate(dt, MUMBAI_TZ, "yyyy-MM-dd")];
}

function IS_LIVE_MARKET_OPEN_NOW_() {
  var now = new Date();
  if (!IS_NSE_TRADING_DAY_(now)) return false;
  var mins = parseInt(Utilities.formatDate(now, MUMBAI_TZ, "HH"), 10) * 60 + parseInt(Utilities.formatDate(now, MUMBAI_TZ, "mm"), 10);
  return (mins >= 555 && mins <= 935);
}

function GET_NEXT_TRADING_DAY_(d) {
  var cur = new Date(d.getTime());
  for (var i = 1; i <= 10; i++) {
    var next = new Date(cur.getTime() + i * 86400000);
    if (IS_NSE_TRADING_DAY_(next)) return next;
  }
  return new Date(cur.getTime() + 86400000);
}

function GET_ACTIVE_OR_NEXT_TRADING_DAY_() {
  var now = new Date();
  var mins = parseInt(Utilities.formatDate(now, MUMBAI_TZ, "HH"), 10) * 60 + parseInt(Utilities.formatDate(now, MUMBAI_TZ, "mm"), 10);
  return (!IS_NSE_TRADING_DAY_(now) || mins > 935) ? GET_NEXT_TRADING_DAY_(now) : now;
}

function MARKET_STATUS(refreshTick) {
  return IS_LIVE_MARKET_OPEN_NOW_() ? "OPEN" : "CLOSED";
}

// ===================== MENU & ANTI-SPAM TRIGGER FIREWALL =====================
function onOpen() {
  PURGE_ROGUE_TRIGGERS_();
  SpreadsheetApp.getUi().createMenu('⚡ Trading System')
    .addItem('🛑 STOP All Automated Telegram Alerts', 'STOP_ALL_TELEGRAM_TRIGGERS')
    .addSeparator()
    .addItem('🔑 Set / Update Credentials', 'SET_CREDENTIALS_UI')
    .addItem('🔑 Quick-update Dhan Token only', 'UPDATE_DHAN_TOKEN_UI')
    .addItem('🧪 Test Connections', 'TEST_CONNECTIONS')
    .addSeparator()
    .addItem('✨ 0. Rebuild Simple & Clean Terminal (All Tabs)', 'REBUILD_ALL_COSMIC_TABS')
    .addItem('🔄 Sync Tomorrow/Today Gann Levels Now', 'AUTO_SYNC_NIFTY_GANN_LEVELS')
    .addItem('♻️ Refresh Live Data Now', 'REFRESH_LIVE_DATA')
    .addItem('⏰ Install Anti-Spam Auto Triggers', 'INSTALL_AUTO_TRIGGERS')
    .addSeparator()
    .addItem('1. Rebuild Simple Main Terminal', 'BUILD_DASHBOARD')
    .addItem('2. Rebuild Gann Levels Tab', 'BUILD_GANN_TAB')
    .addItem('3. Rebuild Trade Journal Tab', 'BUILD_JOURNAL_TAB')
    .addItem('4. Rebuild Self-Learning Tab', 'BUILD_LEARNING_TAB')
    .addSeparator()
    .addItem('🚨 Check Signal & Send Telegram If New', 'CHECK_GANN_PRICE_ALERTS')
    .addItem('🚀 Force Send Full Telegram Summary Now', 'TRIGGER_TELEGRAM_CHECK')
    .addItem('📅 Send 10-Day Astro + Gann Forecast to Telegram', 'PUSH_WEEKLY_FORECAST_TELEGRAM')
    .addToUi();
}

function PURGE_ROGUE_TRIGGERS_() {
  try {
    var allowed = { "AUTO_5MIN_SCAN_TRIGGER": 1, "AUTO_POST_CLOSE_GANN_SYNC_TRIGGER": 1, "ON_TERMINAL_EDIT": 1 };
    var seen = {};
    var triggers = ScriptApp.getProjectTriggers();
    for (var i = 0; i < triggers.length; i++) {
      var fn = triggers[i].getHandlerFunction();
      if (!allowed[fn] || seen[fn]) {
        ScriptApp.deleteTrigger(triggers[i]);
      } else {
        seen[fn] = true;
      }
    }
  } catch (e) { Logger.log("PURGE_ROGUE_TRIGGERS_: " + e); }
}

function STOP_ALL_TELEGRAM_TRIGGERS() {
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    ScriptApp.deleteTrigger(triggers[i]);
  }
  PropertiesService.getScriptProperties().setProperty("TELEGRAM_AUTO_MUTED", "TRUE");
  SpreadsheetApp.getUi().alert("🛑 All automated background triggers in this sheet have been deleted and muted.\n\n(To re-enable 5-min anti-spam alerts later, click '⏰ Install Anti-Spam Auto Triggers'.)");
}

function REBUILD_ALL_COSMIC_TABS() {
  BUILD_GANN_TAB();
  AUTO_SYNC_NIFTY_GANN_LEVELS();
  BUILD_JOURNAL_TAB();
  BUILD_LEARNING_TAB();
  BUILD_DASHBOARD();
}

function INSTALL_AUTO_TRIGGERS() {
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) ScriptApp.deleteTrigger(triggers[i]);

  ScriptApp.newTrigger("AUTO_5MIN_SCAN_TRIGGER").timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger("AUTO_POST_CLOSE_GANN_SYNC_TRIGGER").timeBased().everyDays(1).atHour(16).nearMinute(45).inTimezone(MUMBAI_TZ).create();
  ScriptApp.newTrigger("ON_TERMINAL_EDIT").forSpreadsheet(SpreadsheetApp.getActiveSpreadsheet()).onEdit().create();

  var props = PropertiesService.getScriptProperties();
  props.deleteProperty("TELEGRAM_AUTO_MUTED");
  props.deleteProperty("LAST_TELEGRAM_SIGNAL_HASH");
  props.deleteProperty("LAST_TELEGRAM_PATTERN_SIG");

  SpreadsheetApp.getUi().alert("✅ Anti-Spam Automation Installed!\n\n• Scan every 5 min (market hours only, 15-min cooldown lock)\n• Gann level sync daily after close\n• Auto level re-sync when you change Index dropdown");
}

function AUTO_5MIN_SCAN_TRIGGER() {
  PURGE_ROGUE_TRIGGERS_();
  if (PropertiesService.getScriptProperties().getProperty("TELEGRAM_AUTO_MUTED") === "TRUE") return;
  if (!IS_LIVE_MARKET_OPEN_NOW_()) return;
  REFRESH_TICK_();
  CHECK_GANN_PRICE_ALERTS();
}

function AUTO_POST_CLOSE_GANN_SYNC_TRIGGER() {
  if (!IS_NSE_TRADING_DAY_(new Date())) return;
  AUTO_SYNC_NIFTY_GANN_LEVELS();
}

function ON_TERMINAL_EDIT(e) {
  try {
    if (!e || !e.range) return;
    if (e.range.getSheet().getName() === SHEET_ASTRO && e.range.getA1Notation() === "B6") {
      AUTO_SYNC_NIFTY_GANN_LEVELS(true);
    }
  } catch (err) { Logger.log("ON_TERMINAL_EDIT: " + err); }
}

function REFRESH_TICK_() {
  var s = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_ASTRO);
  if (s) s.getRange("A30").setValue(new Date().getTime());
}

function REFRESH_LIVE_DATA() {
  CLEAR_CACHE_();
  REFRESH_TICK_();
  SpreadsheetApp.flush();
}

// ===================== SHEET BUILDERS =====================
function RESET_SHEET_(sheet) {
  var full = sheet.getRange(1, 1, sheet.getMaxRows(), sheet.getMaxColumns());
  full.breakApart();
  full.clearDataValidations();
  sheet.clear();
  sheet.clearConditionalFormatRules();
}

function PUT_(sheet, a1, content) {
  var r = sheet.getRange(a1);
  if (r.getNumRows() * r.getNumColumns() > 1) r.merge();
  var c = r.getCell(1, 1);
  if (typeof content === "string" && content.charAt(0) === "=") c.setFormula(content); else c.setValue(content);
  return r;
}

function BUILD_GANN_TAB() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_GANN) || ss.insertSheet(SHEET_GANN);
  var qC = 26005.35, qO = 25312.10, wC = 23146.15, wO = 22593.20, dC = 22753.20, dO = 22524.00;
  var eH = 24774.30, eL = 22388.00;
  RESET_SHEET_(sheet);
  sheet.setHiddenGridlines(true);
  try { sheet.setTabColor("#6366F1"); } catch (e) {}

  sheet.setColumnWidth(1, 230); sheet.setColumnWidth(2, 135); sheet.setColumnWidth(3, 18);
  sheet.setColumnWidth(4, 230); sheet.setColumnWidth(5, 135); sheet.setColumnWidth(6, 18);
  sheet.setColumnWidth(7, 245); sheet.setColumnWidth(8, 145);

  var canvas = sheet.getRange("A1:H33");
  canvas.setBackground("#0B0F19").setFontColor("#E2E8F0").setFontFamily("Consolas").setVerticalAlignment("middle").setFontSize(10);
  canvas.setBorder(true, true, true, true, true, true, "#1E293B", SpreadsheetApp.BorderStyle.SOLID);

  PUT_(sheet, "A1:H1", "📐 W.D. GANN SQUARE OF 9 & EIGHTHS (AUTO-SYNCED AFTER MARKET CLOSE)")
    .setBackground("#1E1B4B").setFontColor("#FDE047").setFontSize(13).setFontWeight("bold").setHorizontalAlignment("center")
    .setBorder(true, true, true, true, false, false, "#F59E0B", SpreadsheetApp.BorderStyle.SOLID_MEDIUM);
  sheet.setRowHeight(1, 36);

  var block = function(labelCol, valCol, prefix, name, icon, c, o, biasFormula, biasColor, rLabels, sLabels) {
    sheet.getRange(labelCol + "3:" + labelCol + "5").setValues([[icon + " Prev " + name + " Close:"], [icon + " Prev " + name + " Open:"], ["🔮 " + (name === "D1" ? "Daily Tactical" : name === "WK" ? "Weekly" : "Quarterly") + " Bias:"]])
      .setBackground("#111827").setFontColor("#C4B5FD").setFontWeight("bold");
    sheet.getRange(valCol + "3:" + valCol + "4").setBackground("#1E1B4B").setFontColor("#FDE047").setFontWeight("bold").setHorizontalAlignment("center").setNumberFormat("0.00");
    sheet.getRange(valCol + "3").setValue(c); sheet.getRange(valCol + "4").setValue(o);
    sheet.getRange(valCol + "5").setFormula(biasFormula).setBackground("#0F172A").setFontColor(biasColor).setFontWeight("bold").setHorizontalAlignment("center");

    PUT_(sheet, labelCol + "7:" + valCol + "7", "🔴 " + (name === "D1" ? "DAILY" : name === "WK" ? "WEEKLY" : "QTR (90-DAY)") + " RESISTANCE")
      .setBackground("#450A0A").setFontColor("#FCA5A5").setFontWeight("bold");
    sheet.getRange(labelCol + "8:" + labelCol + "11").setValues(rLabels.map(function(x) { return [x]; })).setBackground("#111827");
    var adds = ["2.00", "1.00", "0.50", "0.25"];
    for (var i = 0; i < 4; i++) sheet.getRange(valCol + (8 + i)).setFormula("=ROUND((SQRT(" + valCol + "3)+" + adds[i] + ")^2, 2)");
    sheet.getRange(valCol + "8:" + valCol + "11").setBackground("#1F1218").setFontColor("#F87171").setFontWeight("bold").setHorizontalAlignment("center").setNumberFormat("0.00");

    PUT_(sheet, labelCol + "13:" + valCol + "13", "🟢 " + (name === "D1" ? "DAILY" : name === "WK" ? "WEEKLY" : "QTR (90-DAY)") + " SUPPORT")
      .setBackground("#064E3B").setFontColor("#6EE7B7").setFontWeight("bold");
    sheet.getRange(labelCol + "14:" + labelCol + "17").setValues(sLabels.map(function(x) { return [x]; })).setBackground("#111827");
    var subs = ["0.25", "0.50", "1.00", "2.00"];
    for (var k = 0; k < 4; k++) sheet.getRange(valCol + (14 + k)).setFormula("=ROUND((SQRT(" + valCol + "3)-" + subs[k] + ")^2, 2)");
    sheet.getRange(valCol + "14:" + valCol + "17").setBackground("#07221B").setFontColor("#4ADE80").setFontWeight("bold").setHorizontalAlignment("center").setNumberFormat("0.00");
  };

  block("A", "B", "Q", "QTR", "🪐", qC, qO, '=IF(B3>B4, "🟢 SECULAR BULL", "🔴 SECULAR BEAR")', "#6EE7B7",
    ["Q-R4 (360°)", "Q-R3 (180°)", "Q-R2 (90°)", "Q-R1 (45°)"], ["Q-S1 (45°)", "Q-S2 (90°)", "Q-S3 (180°)", "Q-S4 (360°)"]);
  block("D", "E", "W", "WK", "🌙", wC, wO, '=IF(E3>E4, "🟢 BULLISH", "🔴 BEARISH")', "#6EE7B7",
    ["W-R4 (360°)", "W-R3 (180°)", "W-R2 (90°)", "W-R1 (45°)"], ["W-S1 (45°)", "W-S2 (90°)", "W-S3 (180°)", "W-S4 (360°)"]);
  block("G", "H", "D", "D1", "☀️", dC, dO, '=IF(H3>H4, "🟢 BUY DIPS", "🔴 BUY SPIKES")', "#FCA5A5",
    ["D-R4 (360°)", "D-R3 (180°)", "D-R2 (90°)", "D-R1 (45° Buy Above)"], ["D-S1 (45° Sell Below)", "D-S2 (90°)", "D-S3 (180°)", "D-S4 (360°)"]);

  sheet.getRange("D22:D23").setValues([["Extreme High (60d):"], ["Extreme Low (60d):"]]).setBackground("#111827").setFontColor("#C4B5FD").setFontWeight("bold");
  sheet.getRange("E22:E23").setBackground("#1E1B4B").setFontColor("#FDE047").setFontWeight("bold").setHorizontalAlignment("center").setNumberFormat("0.00");
  sheet.getRange("E22").setValue(eH); sheet.getRange("E23").setValue(eL);
  sheet.getRange("D29").setValue("50% Pivot:").setBackground("#111827").setFontColor("#C4B5FD").setFontWeight("bold");
  sheet.getRange("E29").setFormula("=ROUND(E23 + (E22-E23)*0.50, 2)").setBackground("#422006").setFontColor("#FACC15").setFontWeight("bold").setHorizontalAlignment("center").setNumberFormat("0.00");
}

function BUILD_JOURNAL_TAB() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_JOURNAL) || ss.insertSheet(SHEET_JOURNAL);
  RESET_SHEET_(sheet); sheet.setHiddenGridlines(true);
  try { sheet.setTabColor("#10B981"); } catch (e) {}
  PUT_(sheet, "A1:H1", "📖 SIMPLE TRADE JOURNAL").setBackground("#1E1B4B").setFontColor("#FDE047").setFontSize(13).setFontWeight("bold").setHorizontalAlignment("center");
  sheet.setRowHeight(1, 36);
  sheet.getRange("A3:H3").setValues([["Date", "Signal", "Entry", "Stop Loss", "Net P/L (₹)", "Followed 2-Bar Rule?", "Mindset", "Lesson Learned"]]).setBackground("#31102F").setFontColor("#FBBF24").setFontWeight("bold");
  sheet.setColumnWidths(1, 8, 140);
  sheet.setColumnWidth(8, 320);
}

function BUILD_LEARNING_TAB() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_LEARN) || ss.insertSheet(SHEET_LEARN);
  RESET_SHEET_(sheet); sheet.setHiddenGridlines(true);
  try { sheet.setTabColor("#EC4899"); } catch (e) {}
  PUT_(sheet, "A1:G1", "🧠 AI SELF-LEARNING LOG").setBackground("#1E1B4B").setFontColor("#FDE047").setFontSize(13).setFontWeight("bold").setHorizontalAlignment("center");
  sheet.setRowHeight(1, 36);
  sheet.getRange("A3:G3").setValues([["Date", "Star", "Element Weather", "Nifty Close", "2-Bar Signal", "Outcome", "Key Takeaway"]]).setBackground("#31102F").setFontColor("#FBBF24").setFontWeight("bold");
  sheet.setColumnWidths(1, 7, 140);
  sheet.setColumnWidth(7, 320);
}

function BUILD_DASHBOARD() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_ASTRO) || ss.insertSheet(SHEET_ASTRO);
  RESET_SHEET_(sheet);
  sheet.setHiddenGridlines(true);
  try { sheet.setTabColor("#F59E0B"); } catch (e) {}
  ss.setActiveSheet(sheet);

  sheet.setColumnWidth(1, 275);
  sheet.setColumnWidth(2, 160);
  sheet.setColumnWidth(3, 160);
  sheet.setColumnWidth(4, 265);
  sheet.setColumnWidth(5, 210);
  sheet.setColumnWidth(6, 300);

  var canvas = sheet.getRange("A1:F43");
  canvas.setBackground("#0B0F19").setFontColor("#E2E8F0").setFontFamily("Consolas").setVerticalAlignment("middle").setFontSize(10);
  canvas.setBorder(true, true, true, true, true, true, "#1E293B", SpreadsheetApp.BorderStyle.SOLID);

  // HEADER
  PUT_(sheet, "A1:F1", "☸ VIPUL QUANTUM TERMINAL  ✦  AUDITED 3-PILLAR EXECUTION  ✦  MUMBAI IST ☸")
    .setBackground("#1E1B4B").setFontColor("#FDE047").setFontSize(13).setFontWeight("bold").setHorizontalAlignment("center")
    .setBorder(true, true, true, true, false, false, "#F59E0B", SpreadsheetApp.BorderStyle.SOLID_MEDIUM);
  sheet.setRowHeight(1, 36);
  sheet.setRowHeight(2, 8);

  // ROW 3: FINAL VERDICT
  sheet.getRange("A3").setValue("🚦 FINAL TRADE SIGNAL:").setBackground("#111827").setFontColor("#FDE047").setFontWeight("bold").setFontSize(11);
  PUT_(sheet, "B3:F3",
    '=IF(MARKET_STATUS($A$30)<>"OPEN","🔒 MARKET CLOSED — Next session plan: Call above "&ROUND(' + GQ_ + 'H11,0)&" | Put below "&ROUND(' + GQ_ + 'H14,0),' +
    'IF(ISNUMBER(GET_DHAN_LIVE_PRICE(B6,$A$30)),SIMPLE_FINAL_VERDICT(B7,B6,B9,B10,' + GQ_ + 'H11,' + GQ_ + 'H10,' + GQ_ + 'H14, C6),' +
    '"⚠️ DATA STALE — Dhan feed unavailable. No signal."))')
    .setBackground("#1E1B4B").setFontColor("#FDE047").setFontWeight("bold").setFontSize(12).setHorizontalAlignment("center").setWrap(true)
    .setBorder(true, true, true, true, false, false, "#F59E0B", SpreadsheetApp.BorderStyle.SOLID_MEDIUM);
  sheet.setRowHeight(3, 40);
  sheet.setRowHeight(4, 8);

  // STEP 1
  PUT_(sheet, "A5:C5", "🔱 STEP 1: CHECK THE 3 MECHANICAL PILLARS").setBackground("#31102F").setFontColor("#FBBF24").setFontWeight("bold").setFontSize(11);
  sheet.setRowHeight(5, 28);

  sheet.getRange("A6:A11").setValues([
    ["🎯 Select Index & Date"], ["⚡ Live Market Price"], ["🏛️ Pillar 1: Trend & Element Bias"],
    ["📐 Pillar 2: Gann Wall Proximity"], ["🕯️ Pillar 3: 5-Min 2-Bar Trigger"], ["🔮 Overall Confidence Score"]
  ]).setBackground("#111827").setFontColor("#C4B5FD").setFontWeight("bold");

  sheet.getRange("B6")
    .setDataValidation(SpreadsheetApp.newDataValidation().requireValueInList(["NIFTY 50", "BANK NIFTY", "FINNIFTY"], true).build())
    .setValue("NIFTY 50").setBackground("#1E1B4B").setFontColor("#FDE047").setFontWeight("bold").setHorizontalAlignment("center");

  var dates = [];
  var baseDate = new Date();
  for (var i = -5; i < 25; i++) {
    var cand = new Date(baseDate.getTime() + (i * 86400000));
    if (IS_NSE_TRADING_DAY_(cand)) dates.push(Utilities.formatDate(cand, MUMBAI_TZ, "yyyy-MM-dd"));
  }
  sheet.getRange("C6")
    .setDataValidation(SpreadsheetApp.newDataValidation().requireValueInList(dates, true).setAllowInvalid(true).build())
    .setValue(Utilities.formatDate(GET_ACTIVE_OR_NEXT_TRADING_DAY_(), MUMBAI_TZ, "yyyy-MM-dd"))
    .setNumberFormat("yyyy-mm-dd")
    .setBackground("#1E1B4B").setFontColor("#FDE047").setFontWeight("bold").setHorizontalAlignment("center");

  PUT_(sheet, "B7:C7", '=LET(p, GET_DHAN_LIVE_PRICE(B6, $A$30), IF(ISNUMBER(p), p, IF(B6="FINNIFTY", ' + GQ_ + 'H3, IFERROR(GOOGLEFINANCE(IF(B6="BANK NIFTY","INDEXNSE:NIFTY_BANK","INDEXNSE:NIFTY_50"),"price"), ' + GQ_ + 'H3))))')
    .setBackground("#172554").setFontColor("#38BDF8").setFontSize(13).setFontWeight("bold").setHorizontalAlignment("center").setNumberFormat("0.00");

  PUT_(sheet, "B8:C8", "=SIMPLE_PILLAR1_WEATHER(C6, " + GQ_ + "E5, " + GQ_ + "H5)").setBackground("#0F172A").setFontColor("#E2E8F0").setFontWeight("bold").setHorizontalAlignment("center").setFontSize(9).setWrap(true);
  PUT_(sheet, "B9:C9", "=SIMPLE_PILLAR2_WALL_CHECK(B7, " + GQ_ + "H11, " + GQ_ + "H10, " + GQ_ + "H14, " + GQ_ + "H15)").setBackground("#0F172A").setFontColor("#FDE047").setFontWeight("bold").setHorizontalAlignment("center").setFontSize(9).setWrap(true);
  PUT_(sheet, "B10:C10", "=GET_DHAN_2BAR_STATE(B6, B7, " + GQ_ + "H11, " + GQ_ + "H14)").setBackground("#111827").setFontColor("#6EE7B7").setFontWeight("bold").setHorizontalAlignment("center").setFontSize(9).setWrap(true);
  PUT_(sheet, "B11:C11", "=COMPUTE_MASTER_CONFLUENCE(C6, B7, " + GQ_ + "B5, " + GQ_ + "E5, " + GQ_ + "H5, " + GQ_ + "E29)").setBackground("#064E3B").setFontColor("#6EE7B7").setFontWeight("bold").setHorizontalAlignment("center").setFontSize(10).setWrap(true);
  sheet.setRowHeight(8, 34); sheet.setRowHeight(9, 34); sheet.setRowHeight(10, 52); sheet.setRowHeight(11, 28);

  // STEP 2
  PUT_(sheet, "D5:F5", "🌌 STEP 2: TODAY'S SIMPLE ASTRO WEATHER & AI ACTION PLAN").setBackground("#1E1B4B").setFontColor("#FBBF24").setFontWeight("bold").setFontSize(11);
  sheet.getRange("D6:D11").setValues([
    ["🌟 Active Star (Nakshatra)"], ["🔥 4-Element Planet Balance"], ["🌙 Moon Phase & Trap Check"],
    ["🔭 Key Planet Aspect Rule"], ["🧠 Plain-English Action Plan"], ["📌 Best Sectors in Focus Today"]
  ]).setBackground("#111827").setFontColor("#FDE68A").setFontWeight("bold");

  PUT_(sheet, "E6:F6", "=SIMPLE_STAR_SUMMARY(C6)").setBackground("#0F172A").setFontColor("#FDE047").setFontWeight("bold").setFontSize(9).setWrap(true);
  PUT_(sheet, "E7:F7", "=SIMPLE_ELEMENT_SUMMARY(C6)").setBackground("#0F172A").setFontColor("#38BDF8").setFontWeight("bold").setFontSize(9).setWrap(true);
  PUT_(sheet, "E8:F8", "=SIMPLE_TRAP_CHECK(C6)").setBackground("#0F172A").setFontColor("#FCA5A5").setFontWeight("bold").setFontSize(9).setWrap(true);
  PUT_(sheet, "E9:F9", "=SIMPLE_ASPECT_RULE(C6)").setBackground("#0F172A").setFontColor("#A7F3D0").setFontSize(9).setWrap(true);
  PUT_(sheet, "E10:F10", "=DECODE_NIFTY_GANN_VEDIC(C6, B7, " + GQ_ + "H11, " + GQ_ + "H14, " + GQ_ + "H5, " + GQ_ + "H10, " + GQ_ + "H15)").setBackground("#1E1B4B").setFontColor("#FEF08A").setWrap(true).setFontWeight("bold").setFontSize(9);
  PUT_(sheet, "E11:F11", "=SIMPLE_SECTOR_FOCUS(C6)").setBackground("#0F172A").setFontColor("#BAE6FD").setFontSize(9).setWrap(true);
  sheet.setRowHeight(12, 10);

  // STEP 3
  PUT_(sheet, "A13:C13", "📐 STEP 3: TODAY'S ADVANCE BUY / SELL LEVELS (AUDITED)").setBackground("#1E1B4B").setFontColor("#FBBF24").setFontWeight("bold").setFontSize(10);
  sheet.setRowHeight(13, 26);
  sheet.getRange("A14:C14").setValues([["Level Type", "Exact Price", "Simple Action Rule"]]).setBackground("#111827").setFontColor("#C4B5FD").setFontWeight("bold");
  sheet.getRange("A15:A20").setValues([
    ["🚀 Upside Target 2 (D-R3 180°)"], ["🎯 Upside Target 1 (D-R2 90°)"], ["🟢 BUY CALL ABOVE (D-R1 45°)"],
    ["🪐 Astro-Degree Magnet Pivot"], ["🔴 BUY PUT BELOW (D-S1 45°)"], ["📉 Downside Targets (D-S2 / S3)"]
  ]).setBackground("#111827").setFontColor("#C4B5FD").setFontWeight("bold");

  var lvl = function(cell, f, bg, fg) {
    sheet.getRange(cell).setFormula(f).setBackground(bg).setFontColor(fg).setFontWeight("bold").setHorizontalAlignment("center").setNumberFormat("0.00");
  };
  lvl("B15", "=" + GQ_ + "H9", "#064E3B", "#6EE7B7");
  lvl("B16", "=" + GQ_ + "H10", "#064E3B", "#6EE7B7");
  lvl("B17", "=" + GQ_ + "H11", "#14532D", "#4ADE80");
  lvl("B18", "=ASTRO_DEGREE_TO_PRICE_LEVEL(C6, " + GQ_ + "H3)", "#31102F", "#FDE047");
  lvl("B19", "=" + GQ_ + "H14", "#450A0A", "#F87171");
  sheet.getRange("B20").setFormula("=ROUND(" + GQ_ + "H15, 0) & \" / \" & ROUND(" + GQ_ + "H16, 0)").setBackground("#450A0A").setFontColor("#FCA5A5").setFontWeight("bold").setHorizontalAlignment("center");

  sheet.getRange("C15:C20").setValues([
    ["Full trend exhaustion; book all Call profits."], ["Book 50% Call profit; or fade if 2-bar fails."],
    ["Wait for 5m 2-Bar Close ABOVE to Buy Call."], ["Sun/Moon degree harmonic magnet for today."],
    ["Wait for 5m 2-Bar Close BELOW to Buy Put."], ["Book Put profits here; or buy 2-bar bounce."]
  ]).setBackground("#0F172A").setFontColor("#E2E8F0").setFontSize(9).setWrap(true);

  // STEP 4
  PUT_(sheet, "D13:F13", "💰 STEP 4: SYMMETRIC BULL/BEAR TRADE PLAN & RISK ENGINE").setBackground("#064E3B").setFontColor("#FDE047").setFontWeight("bold").setFontSize(10);
  sheet.getRange("D14:F14").setValues([["Trade Parameter", "Live Value", "Simple Risk Rule"]]).setBackground("#111827").setFontColor("#6EE7B7").setFontWeight("bold");
  sheet.getRange("D15:D20").setValues([
    ["💎 Trading Capital & Risk %"], ["🧭 Active Trade Direction"], ["🎯 Entry Price (Spot)"],
    ["🛑 Auto Stop-Loss (2-Bar)"], ["🚀 Auto Targets (1:2 & 1:3)"], ["📦 Allowed Lots to Trade"]
  ]).setBackground("#111827").setFontColor("#C4B5FD").setFontWeight("bold");

  var small = function(cell, content) {
    var r = sheet.getRange(cell);
    if (String(content).charAt(0) === "=") r.setFormula(content); else r.setValue(content);
    r.setBackground("#0F172A").setFontColor("#94A3B8").setFontSize(9);
  };

  sheet.getRange("E15").setValue(100000).setBackground("#1E1B4B").setFontColor("#FDE047").setFontWeight("bold").setNumberFormat("₹#,##0").setHorizontalAlignment("center");
  small("F15", '="Max ' + (RISK_PCT * 100) + '% Risk (₹"&TEXT(E15*' + RISK_PCT + ',"#,##0")&" per trade)"');

  sheet.getRange("E16").setFormula(
    '=IF(REGEXMATCH(B3, "BUY PUT|FADE RESISTANCE"), "🔴 SHORT / BUY PUT (PE)", ' +
    'IF(REGEXMATCH(B3, "BUY CALL|FADE SUPPORT"), "🟢 LONG / BUY CALL (CE)", ' +
    'IF(REGEXMATCH(B3, "MARKET CLOSED|DATA STALE|WAIT|RANGE"), "⚪ NO ENTRY — Range/Wait Mode", ' +
    'IF(REGEXMATCH(B10, "BEAR|BELOW|FAILURE"), "🔴 PLAN: SHORT / PUT", ' +
    'IF(REGEXMATCH(B10, "BULL|ABOVE|HOLD"), "🟢 PLAN: LONG / CALL", ' +
    'IF(REGEXMATCH(' + GQ_ + 'H5, "BUY DIPS"), "🟢 BIAS: LONG", "🔴 BIAS: SHORT"))))))')
    .setBackground("#1E1B4B").setFontColor("#FDE047").setFontWeight("bold").setHorizontalAlignment("center").setFontSize(9);
  small("F16", "Follows the Final Signal in B3");

  sheet.getRange("E17").setFormula("=ROUND(B7, 2)").setBackground("#172554").setFontColor("#38BDF8").setFontWeight("bold").setHorizontalAlignment("center").setNumberFormat("0.00");
  small("F17", "Enter only when B3 confirms signal");

  sheet.getRange("E18").setFormula('=IF(REGEXMATCH(E16, "NO ENTRY"), "—", IF(REGEXMATCH(E16, "SHORT"), ROUND(E17 + ' + STOP_POINTS + ', 2), ROUND(E17 - ' + STOP_POINTS + ', 2)))')
    .setBackground("#450A0A").setFontColor("#FCA5A5").setFontWeight("bold").setHorizontalAlignment("center").setNumberFormat("0.00");
  small("F18", '=IF(REGEXMATCH(E16, "NO ENTRY"), "No active trade", IF(REGEXMATCH(E16, "SHORT"), "Stop +' + STOP_POINTS + ' pts ABOVE entry", "Stop -' + STOP_POINTS + ' pts BELOW entry"))');

  sheet.getRange("E19").setFormula('=IF(REGEXMATCH(E16, "NO ENTRY"), "—", IF(REGEXMATCH(E16, "SHORT"), "T1: " & ROUND(E17 - ' + (STOP_POINTS * 2) + ', 0) & " | T2: " & ROUND(E17 - ' + (STOP_POINTS * 3) + ', 0), "T1: " & ROUND(E17 + ' + (STOP_POINTS * 2) + ', 0) & " | T2: " & ROUND(E17 + ' + (STOP_POINTS * 3) + ', 0)))')
    .setBackground("#064E3B").setFontColor("#6EE7B7").setFontWeight("bold").setHorizontalAlignment("center");
  small("F19", '=IF(REGEXMATCH(E16, "NO ENTRY"), "—", "Move SL to " & ROUND(E17, 0) & " at 1:1 profit")');

  sheet.getRange("E20").setFormula('=IF(ISNUMBER(E18), GANN_POSITION_SIZER(E15, ' + RISK_PCT + ', 0, E17, E18, IF(B6="BANK NIFTY", ' + LOT_SIZES["BANK NIFTY"] + ', IF(B6="FINNIFTY", ' + LOT_SIZES["FINNIFTY"] + ', ' + LOT_SIZES["NIFTY 50"] + '))), "—")')
    .setBackground("#172554").setFontColor("#38BDF8").setFontWeight("bold").setHorizontalAlignment("center").setFontSize(9).setWrap(true);
  small("F20", "Lots: Nifty=" + LOT_SIZES["NIFTY 50"] + " | BankNifty=" + LOT_SIZES["BANK NIFTY"] + " | FinNifty=" + LOT_SIZES["FINNIFTY"]);
  sheet.setRowHeight(20, 34);
  sheet.setRowHeight(21, 10);

  // 5 DYNAMIC TIME WINDOWS
  PUT_(sheet, "A22:F22", "⏰ TODAY'S 5 DYNAMIC GANN + KP/VEDIC INTRADAY REVERSAL WINDOWS (MUMBAI IST)").setBackground("#31102F").setFontColor("#FBBF24").setFontWeight("bold").setFontSize(10);
  sheet.setRowHeight(22, 26);
  sheet.getRange("A23:F23").setValues([["Transit / Confluence Type", "Exact IST Time", "Active Lord", "Exact Astro-Gann Trigger", "Best Action to Take", "Best Sector Focus"]]).setBackground("#111827").setFontColor("#C4B5FD").setFontWeight("bold");
  sheet.getRange("A24:F28").setBackground("#0F172A").setFontSize(9).setWrap(true);
  sheet.getRange("B24:B28").setFontColor("#38BDF8").setFontWeight("bold").setHorizontalAlignment("center");
  sheet.getRange("C24:C28").setFontColor("#FDE047").setFontWeight("bold").setHorizontalAlignment("center");
  sheet.getRange("A24").setFormula("=SIMPLE_5_TIME_WINDOWS(C6, " + GQ_ + "H3)");

  PUT_(sheet, "A29:F29", '=IF(ISNUMBER(GET_DHAN_LIVE_PRICE(B6, $A$30)), "✅ Dhan live feed OK", "⚠️ Dhan feed unavailable") & IF(MARKET_STATUS($A$30)="OPEN", "", "  |  🔒 Market closed — price shown is last close")')
    .setBackground("#0B0F19").setFontColor("#94A3B8").setFontSize(9).setHorizontalAlignment("center");
  sheet.getRange("A30").setValue(new Date().getTime()).setFontColor("#0B0F19").setBackground("#0B0F19");

  // STEP 5: 10-DAY FORWARD FINANCIAL ASTROLOGY & GANN LEVELS ROADMAP
  PUT_(sheet, "A31:F31", "📅 STEP 5: 10-DAY FORWARD FINANCIAL ASTROLOGY & GANN SQUARE OF 9 LEVELS ROADMAP")
    .setBackground("#1E1B4B").setFontColor("#FDE047").setFontWeight("bold").setFontSize(10);
  sheet.setRowHeight(31, 26);
  sheet.getRange("A32:F32").setValues([[
    "Trading Date & Star (Lord)", "4-Element & Regime / Trap", "Astro-Gann Pivot",
    "🟢 Buy Call (+45°) | 🎯 T1 / T2", "🔴 Buy Put (-45°) | 📉 T1 / T2", "Top Intraday Turn & Sector"
  ]]).setBackground("#111827").setFontColor("#C4B5FD").setFontWeight("bold");
  sheet.getRange("A33:F42").setBackground("#0F172A").setFontSize(9).setWrap(true);
  sheet.getRange("C33:C42").setFontColor("#FDE047").setFontWeight("bold").setHorizontalAlignment("center").setNumberFormat("0.00");
  sheet.getRange("D33:D42").setFontColor("#4ADE80").setFontWeight("bold").setHorizontalAlignment("center");
  sheet.getRange("E33:E42").setFontColor("#F87171").setFontWeight("bold").setHorizontalAlignment("center");
  sheet.getRange("A33").setFormula("=WEEKLY_ASTRO_GANN_FORECAST(C6, " + GQ_ + "H3)");
}

// ===================== FORMULA LOGIC (RANGE & CHOP AWARE) =====================
function DEG_TO_SQ9_PRICE_(deg, anchor) {
  var angleFactor = (((deg % 180.0) + 180.0) % 180.0) / 180.0;
  var baseRoot = Math.floor(Math.sqrt(anchor));
  var c0 = Math.pow(baseRoot - 1 + angleFactor, 2);
  var c1 = Math.pow(baseRoot + angleFactor, 2);
  var c2 = Math.pow(baseRoot + 1 + angleFactor, 2);
  var best = Math.abs(c1 - anchor) < Math.abs(c0 - anchor) ? c1 : c0;
  if (Math.abs(c2 - anchor) < Math.abs(best - anchor)) best = c2;
  return best;
}

function ASTRO_DEGREE_TO_PRICE_LEVEL(dateStr, anchorPrice) {
  var p = parseFloat(anchorPrice);
  if (isNaN(p) || p <= 0) return "N/A";
  var eph = COMPUTE_MUMBAI_EPHEMERIS_(dateStr, 9, 15);
  var best = DEG_TO_SQ9_PRICE_(eph.planets.Sun, p);
  return parseFloat(best.toFixed(2));
}

function WEEKLY_ASTRO_GANN_FORECAST(startDate, anchorPrice) {
  var p = parseFloat(anchorPrice) || 22753.20;
  var dp = DATE_PARTS_(startDate);
  var cur = new Date(Date.UTC(dp.y, dp.mo - 1, dp.d, 12, 0, 0));
  var out = [];

  var count = 0;
  for (var i = 0; i < 22 && count < 10; i++) {
    var d = new Date(cur.getTime() + i * 86400000);
    if (!IS_NSE_TRADING_DAY_(d)) continue;
    var dStr = Utilities.formatDate(d, MUMBAI_TZ, "yyyy-MM-dd (EEE)");
    var rawDate = Utilities.formatDate(d, MUMBAI_TZ, "yyyy-MM-dd");
    var eph = COMPUTE_MUMBAI_EPHEMERIS_(rawDate, 9, 15);
    var meta = NAKSHATRA_SIMPLE_[eph.nakshatra] || { lord: "Sun", style: "Trend" };
    var el = eph.elements;

    var sunP  = DEG_TO_SQ9_PRICE_(eph.planets.Sun, p);
    var moonP = DEG_TO_SQ9_PRICE_(eph.planets.Moon, p);
    var lordP = DEG_TO_SQ9_PRICE_(eph.planets[meta.lord] || eph.planets.Sun, p);
    var piv   = 0.40 * sunP + 0.35 * moonP + 0.25 * lordP;
    var rPiv  = Math.sqrt(piv);

    var call45  = Math.pow(rPiv + 0.25, 2);
    var call90  = Math.pow(rPiv + 0.50, 2);
    var call180 = Math.pow(rPiv + 1.00, 2);
    var put45   = Math.pow(rPiv - 0.25, 2);
    var put90   = Math.pow(rPiv - 0.50, 2);
    var put180  = Math.pow(rPiv - 1.00, 2);

    var isRange = (el.air + el.water >= 4) || (eph.vakri.length >= 2);
    var regime = (eph.isBhadra ? "⚠️ BHADRA | " : "") + (isRange ? "🛡️ RANGE/FADE" : "🔥 TREND");
    var wins = SIMPLE_5_TIME_WINDOWS(rawDate, p);
    var topWin = wins && wins.length > 0 ? (wins[0][1] + " (" + wins[0][2] + ")") : "10:15 AM";

    out.push([
      dStr + " — " + eph.nakshatra + " (" + meta.lord + ")",
      "F:" + el.fire + " E:" + el.earth + " A:" + el.air + " W:" + el.water + " | " + regime,
      Math.round(piv * 100) / 100,
      " > " + Math.round(call45) + " | T1:" + Math.round(call90) + " T2:" + Math.round(call180),
      " < " + Math.round(put45) + " | T1:" + Math.round(put90) + " T2:" + Math.round(put180),
      topWin + " | " + VEDIC_PLANET_SECTOR(meta.lord)
    ]);
    count++;
  }
  return out;
}

function SIMPLE_FINAL_VERDICT(livePrice, indexName, wallText, twoBarText, r1, r2, s1, dateStr) {
  var p = parseFloat(livePrice), R1 = parseFloat(r1), R2 = parseFloat(r2), S1 = parseFloat(s1);
  if (isNaN(p) || isNaN(R1) || isNaN(R2) || isNaN(S1)) return "⚠️ DATA UNAVAILABLE";

  var eph = COMPUTE_MUMBAI_EPHEMERIS_(dateStr || new Date(), 9, 15);
  var isRangeDay = (eph.elements.air + eph.elements.water >= 4) || (eph.vakri.length >= 2);
  var tb = String(twoBarText || "");

  if (tb.indexOf("RES TEST FAILURE") >= 0) {
    return "🔴 FADE RESISTANCE (D-R1 " + Math.round(R1) + ") — Rejection Confirmed (Range Fade Mode)";
  }
  if (tb.indexOf("SUP TEST HOLD") >= 0) {
    return "🟢 FADE SUPPORT (D-S1 " + Math.round(S1) + ") — Bounce Confirmed (Range Fade Mode)";
  }

  if (isRangeDay) {
    if (Math.abs(p - R1) <= 10 || Math.abs(p - R2) <= 10) {
      return "🛡️ RANGE / FADE MODE — Price at Resistance Wall (" + Math.round(p) + "). Look for 2-bar rejection shorts, do NOT chase breakouts.";
    }
    if (Math.abs(p - S1) <= 10) {
      return "🛡️ RANGE / FADE MODE — Price at Support Floor (" + Math.round(p) + "). Look for 2-bar bounce longs, do NOT chase breakdowns.";
    }
    return "⏳ RANGE-BOUND MARKET — Air/Water or Retrograde friction active. Fade outer walls (" + Math.round(S1) + " - " + Math.round(R1) + "), ignore mid-range chop.";
  }

  var bull = tb.indexOf("BULL BREAKOUT") >= 0 || tb.indexOf("ABOVE BUY LEVEL") >= 0;
  var bear = tb.indexOf("BEAR BREAKDOWN") >= 0 || tb.indexOf("BELOW SELL LEVEL") >= 0;

  if (bull && p > R1) return "🟢 BUY CALL (CE) SIGNAL — Trend Breakout Above Resistance";
  if (bear && p < S1) return "🔴 BUY PUT (PE) SIGNAL — Trend Breakdown Below Support";

  return "⏳ WAIT / NO TRADE — Awaiting wall test or 2-bar trigger (Support: " + Math.round(S1) + " | Resistance: " + Math.round(R1) + ")";
}

function SIMPLE_PILLAR1_WEATHER(dateStr, wBias, dBias) {
  var eph = COMPUTE_MUMBAI_EPHEMERIS_(dateStr, 9, 15);
  var el = eph.elements;
  var isBullD1 = String(dBias).indexOf("BUY DIPS") >= 0;
  var isBullW1 = String(wBias).indexOf("BULL") >= 0;

  var isRangeWeather = (el.air + el.water >= 4) || (eph.vakri.length >= 2);
  var weather = isRangeWeather ? "🛡️ RANGE / FADE WALLS DAY (Air/Water/Vakri)" : "🔥 TREND / MOMENTUM DAY (Fire/Air Dominant)";
  var trend = (isBullD1 && isBullW1) ? "🟢 BARS BULLISH" : "🔴 BARS BEARISH";
  return trend + " + " + weather;
}

function SIMPLE_PILLAR2_WALL_CHECK(price, r1, r2, s1, s2) {
  var p = parseFloat(price), R1 = parseFloat(r1), R2 = parseFloat(r2), S1 = parseFloat(s1), S2 = parseFloat(s2);
  if (isNaN(p) || isNaN(R1) || isNaN(R2) || isNaN(S1)) return "⚠️ DATA UNAVAILABLE";
  if (Math.abs(p - R2) <= 8) return "🎯 AT RESISTANCE: D-R2 Target Wall (" + Math.round(R2) + ")";
  if (Math.abs(p - R1) <= 8) return "🎯 AT RESISTANCE: D-R1 Buy Level (" + Math.round(R1) + ")";
  if (Math.abs(p - S1) <= 8) return "🎯 AT SUPPORT: D-S1 Sell Wall (" + Math.round(S1) + ")";
  return "⏳ MID-RANGE (Sup: " + Math.round(S1) + " | Res: " + Math.round(R1) + ")";
}

function SIMPLE_STAR_SUMMARY(dateStr) {
  var eph = COMPUTE_MUMBAI_EPHEMERIS_(dateStr, 9, 15);
  var meta = NAKSHATRA_SIMPLE_[eph.nakshatra] || { lord: "Sun", style: "Trend", advice: "Trade walls." };
  return eph.nakshatra + " (Ruled by " + meta.lord + ") — " + meta.style;
}

function SIMPLE_ELEMENT_SUMMARY(dateStr) {
  var el = COMPUTE_MUMBAI_EPHEMERIS_(dateStr, 9, 15).elements;
  var list = [["Fire", el.fire], ["Earth", el.earth], ["Air", el.air], ["Water", el.water]];
  list.sort(function(a, b) { return b[1] - a[1]; });
  var label = list[0][0] + (list[1][1] > 0 ? "/" + list[1][0] : "") + " dominant";
  return "Fire:" + el.fire + " Earth:" + el.earth + " Air:" + el.air + " Water:" + el.water + " → " + label;
}

function SIMPLE_TRAP_CHECK(dateStr) {
  var eph = COMPUTE_MUMBAI_EPHEMERIS_(dateStr, 9, 15);
  var e = eph.elongation;
  var phase = (e < 12 || e >= 348) ? "🌑 New Moon" : ((e >= 168 && e <= 192) ? "🌕 Full Moon" : (e < 180 ? "🌒 Waxing" : "🌘 Waning"));
  if (phase.indexOf("New") >= 0 || phase.indexOf("Full") >= 0) return phase + " — Whipsaw Zone, trade light";
  if (eph.isBhadra) return phase + " — ⚠️ Bhadra (Vishti) Karana: expect traps";
  return phase + " — ✅ NO TRAP TODAY, Clean Structure";
}

function SIMPLE_ASPECT_RULE(dateStr) {
  var eph = COMPUTE_MUMBAI_EPHEMERIS_(dateStr, 9, 15);
  if (!eph.vakri.length) return "🟢 Clean Planetary Spacing (no retrogrades)";
  var merc = eph.vakri.join(",").indexOf("Mercury") >= 0;
  return "🔄 Retrograde: " + eph.vakri.join(", ") + (merc ? " — Mercury (Reversal Setup)" : "");
}

function SIMPLE_SECTOR_FOCUS(dateStr) {
  var meta = NAKSHATRA_SIMPLE_[COMPUTE_MUMBAI_EPHEMERIS_(dateStr, 9, 15).nakshatra] || { lord: "Sun" };
  return VEDIC_PLANET_SECTOR(meta.lord) + " (Star Lord: " + meta.lord + ")";
}

function VEDIC_PLANET_SECTOR(planet) {
  var map = {
    "Sun": "PSU / Energy / Power", "Moon": "FMCG / Dairy / Consumer",
    "Mars": "Realty / Defence / Metals", "Mercury": "IT / Telecom / Media",
    "Jupiter": "Banking / Financials / NBFC", "Venus": "Auto / Luxury / Durables",
    "Saturn": "Infra / Oil & Gas / Mining", "Rahu": "Tech / Aviation / Pharma",
    "Ketu": "Pharma / Chemicals"
  };
  return map[String(planet)] || "Broad Market";
}

function VEDIC_HORA_SEQUENCE(dateStr) {
  var dp = DATE_PARTS_(dateStr);
  var wd = new Date(Date.UTC(dp.y, dp.mo - 1, dp.d)).getUTCDay();
  var dayLords = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"];
  var chaldean = ["Saturn", "Jupiter", "Mars", "Sun", "Venus", "Mercury", "Moon"];
  var idx = chaldean.indexOf(dayLords[wd]);
  var out = [];
  for (var i = 0; i < 12; i++) out.push([chaldean[(idx + i) % 7]]);
  return out;
}

function SUN_TIMES_MINS_(dp) {
  var N = Math.floor((Date.UTC(dp.y, dp.mo - 1, dp.d) - Date.UTC(dp.y, 0, 0)) / 86400000);
  var rad = Math.PI / 180, lngHour = MUMBAI_LNG / 15;
  var calc = function(rising) {
    var t = N + ((rising ? 6 : 18) - lngHour) / 24;
    var M = 0.9856 * t - 3.289;
    var L = (M + 1.916 * Math.sin(M * rad) + 0.020 * Math.sin(2 * M * rad) + 282.634) % 360;
    if (L < 0) L += 360;
    var RA = Math.atan(0.91764 * Math.tan(L * rad)) / rad;
    RA = ((RA % 360) + 360) % 360;
    RA = (RA + (Math.floor(L / 90) * 90 - Math.floor(RA / 90) * 90)) / 15;
    var sinDec = 0.39782 * Math.sin(L * rad);
    var cosDec = Math.cos(Math.asin(sinDec));
    var cosH = (Math.cos(90.833 * rad) - sinDec * Math.sin(MUMBAI_LAT * rad)) / (cosDec * Math.cos(MUMBAI_LAT * rad));
    if (cosH > 1 || cosH < -1) return NaN;
    var H = (rising ? 360 - Math.acos(cosH) / rad : Math.acos(cosH) / rad) / 15;
    var T = H + RA - 0.06571 * t - 6.622;
    var UT = (((T - lngHour) % 24) + 24) % 24;
    return ((UT + 5.5) % 24) * 60;
  };
  var sr = calc(true), ssn = calc(false);
  if (isNaN(sr) || isNaN(ssn) || ssn <= sr) return { sr: 396, ss: 1089 };
  return { sr: sr, ss: ssn };
}

// ===================== DYNAMIC VEDIC, KP & GANN PRICE-TIME WINDOWS =====================
function COMPUTE_LAGNA_SID_(jd_utc, ayanamsa) {
  var T = (jd_utc - 2451545.0) / 36525.0;
  var gmst = ((280.46061837 + 360.98564736629 * (jd_utc - 2451545.0) + 0.000387933 * T * T) % 360.0 + 360.0) % 360.0;
  var ramc = (gmst + MUMBAI_LNG) % 360.0;
  var rad = Math.PI / 180.0;
  var eps = (23.4392911 - 0.0130042 * T) * rad;
  var y = Math.cos(ramc * rad);
  var x = -Math.sin(ramc * rad) * Math.cos(eps) - Math.tan(MUMBAI_LAT * rad) * Math.sin(eps);
  var ascTrop = ((Math.atan2(y, x) / rad) % 360.0 + 360.0) % 360.0;
  return ((ascTrop - ayanamsa) % 360.0 + 360.0) % 360.0;
}

function GET_KP_SUBLORD_(moonSid) {
  var lords = ["Ketu", "Venus", "Sun", "Moon", "Mars", "Rahu", "Jupiter", "Saturn", "Mercury"];
  var years = [7, 20, 6, 10, 7, 18, 16, 19, 17];
  var nakSpan = 360.0 / 27.0;
  var nakIdx = Math.floor(moonSid / nakSpan);
  var rem = moonSid - (nakIdx * nakSpan);
  var startIdx = nakIdx % 9;
  var acc = 0.0;
  for (var i = 0; i < 9; i++) {
    var idx = (startIdx + i) % 9;
    var subSpan = nakSpan * (years[idx] / 120.0);
    if (rem < acc + subSpan) return lords[idx];
    acc += subSpan;
  }
  return lords[(startIdx + 8) % 9];
}

function SIMPLE_5_TIME_WINDOWS(dateStr, anchorPrice) {
  var dp = DATE_PARTS_(dateStr);
  var ephStart = COMPUTE_MUMBAI_EPHEMERIS_(dateStr, 9, 15);
  var ephEnd   = COMPUTE_MUMBAI_EPHEMERIS_(dateStr, 15, 30);

  var mn1 = ephStart.planets.Moon;
  var mn2 = ephEnd.planets.Moon;
  if (mn2 < mn1) mn2 += 360.0;

  var jd1 = ((Date.UTC(dp.y, dp.mo - 1, dp.d, 9, 15, 0) - 5.5 * 3600 * 1000) / 86400000.0) + 2440587.5;
  var jd2 = ((Date.UTC(dp.y, dp.mo - 1, dp.d, 15, 30, 0) - 5.5 * 3600 * 1000) / 86400000.0) + 2440587.5;
  var ayan = 23.853056 + (jd1 - 2451545.0) * (50.290966 / 3600.0 / 365.25);

  var signNames = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"];
  var signLords = ["Mars", "Venus", "Mercury", "Moon", "Sun", "Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Saturn", "Jupiter"];

  var events = [];
  var prevSign = null, prevPada = null, prevSub = null;

  for (var idx = 0; idx <= 375; idx++) {
    var frac = idx / 375.0;
    var mn = (mn1 + (mn2 - mn1) * frac) % 360.0;
    var jdCur = jd1 + (jd2 - jd1) * frac;
    var asc = COMPUTE_LAGNA_SID_(jdCur, ayan);

    var signIdx = Math.floor(asc / 30.0);
    var padaIdx = Math.floor(mn / (360.0 / 108.0));
    var subLord = GET_KP_SUBLORD_(mn);
    var mTotal = 555 + idx;

    if (idx > 0 && mTotal < 925) {
      if (padaIdx !== prevPada) {
        events.push({
          min: mTotal, type: "🌙 Moon Pada Shift", lord: subLord,
          desc: "Moon Pada " + ((prevPada % 4) + 1) + "→" + ((padaIdx % 4) + 1) + " (Navamsa turn)",
          action: "Intraday structure shift; watch Gann wall for 2-bar reversal", priority: 3.0
        });
      }
      if (subLord !== prevSub) {
        events.push({
          min: mTotal, type: "🔮 KP Sub-Lord Flip", lord: subLord,
          desc: "Moon Sub-Lord: " + prevSub + " → " + subLord,
          action: "Micro-trend shift; enter only on 5m 2-Bar confirmation", priority: 2.5
        });
      }
      if (signIdx !== prevSign) {
        events.push({
          min: mTotal, type: "🌅 Lagna Ingress", lord: signLords[signIdx],
          desc: "Mumbai Lagna: " + signNames[prevSign] + " → " + signNames[signIdx],
          action: "Institutional sector rotation & flow shift window", priority: 1.8
        });
      }
    }
    prevSign = signIdx; prevPada = padaIdx; prevSub = subLord;
  }

  var pAnchor = parseFloat(anchorPrice);
  if (isNaN(pAnchor) || pAnchor <= 0) {
    try {
      var gSheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_GANN);
      if (gSheet) pAnchor = parseFloat(gSheet.getRange("H3").getValue());
    } catch (e) {}
  }
  if (!isNaN(pAnchor) && pAnchor > 0) {
    var root = Math.sqrt(pAnchor);
    var fracP = root - Math.floor(root);
    var degP = fracP * 360.0;
    var baseOff = Math.round(degP % 180.0);
    for (var k = 0; k < 4; k++) {
      var gm = 555 + ((baseOff + k * 90) % 360);
      var gDeg = Math.round((degP + k * 90) % 360);
      if (gm >= 565 && gm <= 915) {
        var mnAtG = (mn1 + (mn2 - mn1) * ((gm - 555) / 375.0)) % 360.0;
        events.push({
          min: gm, type: "📐 Gann Price-Time Sq", lord: GET_KP_SUBLORD_(mnAtG),
          desc: "Gann Sq9 Time Angle (" + gDeg + "° from √" + Math.round(pAnchor) + ")",
          action: "Price-Time square harmonic; watch for pin/rejection at wall",
          priority: (k % 2 === 0 ? 2.2 : 1.6)
        });
      }
    }
  }

  events.sort(function(a, b) { return a.min - b.min; });

  var merged = [];
  for (var i = 0; i < events.length; i++) {
    var ev = events[i];
    if (merged.length > 0 && Math.abs(ev.min - merged[merged.length - 1].min) <= 10) {
      var last = merged[merged.length - 1];
      last.min = Math.round((last.min + ev.min) / 2);
      var hasGann = (last.type.indexOf("Gann") >= 0) || (ev.type.indexOf("Gann") >= 0);
      last.type = hasGann ? "⚡ GANN + ASTRO CONFLUENCE" : "⚡ DOUBLE ASTRO CONFLUENCE";
      if (last.desc.indexOf(ev.desc) === -1) last.desc += " + " + ev.desc;
      if (ev.priority >= last.priority) last.lord = ev.lord;
      last.action = "High-conviction reversal/breakout cluster — trade 2-bar trigger";
      last.priority = Math.max(last.priority, ev.priority) + 2.2;
    } else {
      merged.push(ev);
    }
  }

  merged.sort(function(a, b) { return b.priority - a.priority || a.min - b.min; });
  var top5 = merged.slice(0, 5).sort(function(a, b) { return a.min - b.min; });

  var fmtMin = function(m) {
    m = Math.round(m);
    var h24 = Math.floor(m / 60) % 24, mn = m % 60;
    return (h24 % 12 || 12) + ":" + (mn < 10 ? "0" : "") + mn + " " + (h24 >= 12 ? "PM" : "AM");
  };

  var out = [];
  for (var r = 0; r < top5.length; r++) {
    var w = top5[r];
    out.push([
      (r + 1) + ". " + w.type,
      fmtMin(w.min),
      w.lord,
      w.desc,
      w.action,
      VEDIC_PLANET_SECTOR(w.lord)
    ]);
  }
  return out;
}

function DECODE_NIFTY_GANN_VEDIC(dateStr, currentPrice, res1, sup1, dBias, res2, sup2) {
  var buyAbove = parseFloat(res1), sellBelow = parseFloat(sup1);
  var t1 = parseFloat(res2), t2 = parseFloat(sup2);
  if (isNaN(buyAbove) || isNaN(sellBelow)) return "⚠️ DATA UNAVAILABLE";
  var up = isNaN(t1) ? buyAbove + 75 : t1;
  var dn = isNaN(t2) ? sellBelow - 75 : t2;
  return "👉 CALL PLAN: Buy Call on 2-bar close above " + Math.round(buyAbove) + " (Target " + Math.round(up) + "). 👉 PUT PLAN: Buy Put on 2-bar breakdown below " + Math.round(sellBelow) + " (Target " + Math.round(dn) + ").";
}

function GANN_POSITION_SIZER(capital, riskPct, buffer, entry, stop, lotSize) {
  var cap = parseFloat(capital) || 0;
  var risk = cap * (parseFloat(riskPct) || RISK_PCT);
  var perLot = Math.abs((parseFloat(entry) || 0) - (parseFloat(stop) || 0)) * (parseFloat(lotSize) || 65);
  if (perLot <= 0 || risk <= 0) return "Check inputs";
  var lots = Math.floor(risk / perLot);
  if (lots < 1) return "0 Lots (1 lot risks ₹" + Math.round(perLot).toLocaleString("en-IN") + " > limit)";
  return lots + " Lot" + (lots > 1 ? "s" : "") + " (Risk ₹" + Math.round(lots * perLot).toLocaleString("en-IN") + ")";
}

function COMPUTE_MASTER_CONFLUENCE(dateStr, price, qBias, wBias, dBias, pivot50) {
  var eph = COMPUTE_MUMBAI_EPHEMERIS_(dateStr, 9, 15);
  var score = 60;
  if (String(wBias).indexOf("BULL") >= 0 && String(dBias).indexOf("BUY DIPS") >= 0) score += 15;
  else if (String(wBias).indexOf("BEAR") >= 0 && String(dBias).indexOf("BUY SPIKES") >= 0) score += 15;
  if (eph.elements.fire + eph.elements.air >= 4) score += 10;
  if (eph.isBhadra) score -= 12;
  score = Math.min(95, Math.max(30, score));
  var tag = score >= 75 ? "🟢 STRONG EDGE" : (score >= 55 ? "🟡 MODERATE EDGE" : "🔴 CAUTION / TRAP ZONE");
  return tag + " (" + score + "% checklist score)";
}

// ===================== DHAN: LIVE PRICE & 5-MIN 2-BAR =====================
function INDEX_SEC_ID_(name) {
  var s = String(name || "").toUpperCase();
  if (s.indexOf("BANK") >= 0) return 25;
  if (s.indexOf("FIN") >= 0) return 27;
  return 13;
}

function CACHED_FETCH_(key, ttl, fn) {
  var cache = CacheService.getScriptCache();
  var hit = cache.get(key);
  if (hit !== null) { try { return JSON.parse(hit); } catch (e) {} }
  var v = fn();
  if (v !== null && v !== undefined) { try { cache.put(key, JSON.stringify(v), ttl); } catch (e) {} }
  return v;
}

function GET_DHAN_LIVE_PRICE(indexName, refreshTick) {
  var c = CREDS_();
  if (!c.token || !c.client) return "NO_CREDENTIALS";
  var secId = INDEX_SEC_ID_(indexName);
  var v = CACHED_FETCH_("ltp_" + secId, 15, function() {
    try {
      var resp = UrlFetchApp.fetch("https://api.dhan.co/v2/marketfeed/ltp", {
        method: "post",
        contentType: "application/json",
        headers: { "access-token": c.token, "client-id": c.client, "Accept": "application/json" },
        payload: JSON.stringify({ "IDX_I": [secId] }),
        muteHttpExceptions: true
      });
      if (resp.getResponseCode() !== 200) return null;
      var j = JSON.parse(resp.getContentText());
      var lp = j && j.data && j.data.IDX_I && j.data.IDX_I[String(secId)] && j.data.IDX_I[String(secId)].last_price;
      return lp ? parseFloat(lp) : null;
    } catch (e) { return null; }
  });
  return (typeof v === "number" && v > 0) ? v : "FEED_ERROR";
}

function NORMALIZE_TS_MS_(ts) {
  if (ts < 946684800) ts += 315532800;
  return ts > 1e11 ? ts : ts * 1000;
}

function FETCH_5MIN_BARS_(indexName) {
  var c = CREDS_();
  var secId = INDEX_SEC_ID_(indexName);
  return CACHED_FETCH_("bars_" + secId, 45, function() {
    try {
      var now = new Date();
      var toDate = Utilities.formatDate(now, MUMBAI_TZ, "yyyy-MM-dd HH:mm:ss");
      var fromDate = Utilities.formatDate(new Date(now.getTime() - 4 * 86400000), MUMBAI_TZ, "yyyy-MM-dd") + " 09:15:00";
      var resp = UrlFetchApp.fetch("https://api.dhan.co/v2/charts/intraday", {
        method: "post",
        contentType: "application/json",
        headers: { "access-token": c.token, "client-id": c.client, "Accept": "application/json" },
        payload: JSON.stringify({ securityId: String(secId), exchangeSegment: "IDX_I", instrument: "INDEX", interval: "5", fromDate: fromDate, toDate: toDate }),
        muteHttpExceptions: true
      });
      if (resp.getResponseCode() !== 200) return null;
      var j = JSON.parse(resp.getContentText());
      if (!j || !j.close || !j.timestamp) return null;
      var bars = [];
      for (var i = 0; i < j.close.length; i++) {
        var tms = NORMALIZE_TS_MS_(j.timestamp[i]);
        if (now.getTime() - tms < 300000) continue;
        bars.push({ t: tms, h: j.high[i], l: j.low[i], c: j.close[i] });
      }
      return bars;
    } catch (e) { return null; }
  });
}

function GET_DHAN_2BAR_STATE(indexName, currentPrice, res1, sup1) {
  var p = parseFloat(currentPrice), r1 = parseFloat(res1), s1 = parseFloat(sup1);
  if (isNaN(p) || isNaN(r1) || isNaN(s1)) return "⚠️ DATA UNAVAILABLE";

  var open = IS_LIVE_MARKET_OPEN_NOW_();
  var cr = CREDS_();
  if (open && cr.token && cr.client) {
    var bars = FETCH_5MIN_BARS_(indexName);
    if (bars) {
      var todayStr = Utilities.formatDate(new Date(), MUMBAI_TZ, "yyyy-MM-dd");
      var tb = bars.filter(function(b) { return Utilities.formatDate(new Date(b.t), MUMBAI_TZ, "yyyy-MM-dd") === todayStr; });
      if (tb.length < 3) return "⏳ WAITING — need 3 completed 5-min bars today (" + tb.length + "/3)";

      var n = tb.length;
      var c0 = tb[n - 1].c, h0 = tb[n - 1].h, l0 = tb[n - 1].l;
      var h1 = tb[n - 2].h, l1 = tb[n - 2].l;
      var h2 = tb[n - 3].h, l2 = tb[n - 3].l;

      if (l1 <= s1 + 6 && c0 > h1) return "🟢 2-BAR SUP TEST HOLD @ " + s1.toFixed(0);
      if (h1 >= r1 - 6 && c0 < l1) return "🔴 2-BAR RES TEST FAILURE @ " + r1.toFixed(0);
      if (c0 > h1 && c0 > h2) return "🟢 2-BAR BULL BREAKOUT (> " + Math.max(h1, h2).toFixed(0) + ")";
      if (c0 < l1 && c0 < l2) return "🔴 2-BAR BEAR BREAKDOWN (< " + Math.min(l1, l2).toFixed(0) + ")";
      return "⏳ INSIDE 2-BAR RANGE (" + Math.min(l1, l0).toFixed(0) + " - " + Math.max(h1, h0).toFixed(0) + ")";
    }
  }

  var tag = open ? " (price only)" : " (market closed)";
  if (p > r1) return "🟢 ABOVE BUY LEVEL (" + r1.toFixed(0) + ") — BULLISH" + tag;
  if (p < s1) return "🔴 BELOW SELL LEVEL (" + s1.toFixed(0) + ") — BEARISH" + tag;
  return "⏳ BETWEEN SUP (" + s1.toFixed(0) + ") & RES (" + r1.toFixed(0) + ")" + tag;
}

// ===================== AUTO GANN LEVEL SYNC =====================
function FETCH_HISTORICAL_DAILY_CANDLES_(indexName) {
  var cleanIdx = String(indexName || "NIFTY 50").toUpperCase();
  var secId = INDEX_SEC_ID_(cleanIdx);
  var yahooSym = "%5ENSEI";
  if (secId === 25) yahooSym = "%5ENSEBANK";
  else if (secId === 27) yahooSym = "NIFTY_FIN_SERVICE.NS";

  var c = CREDS_();
  var candles = [];
  var now = new Date();
  var toDate = Utilities.formatDate(new Date(now.getTime() + 86400000), MUMBAI_TZ, "yyyy-MM-dd");
  var fromDate = Utilities.formatDate(new Date(now.getTime() - 220 * 86400000), MUMBAI_TZ, "yyyy-MM-dd");

  if (c.token && c.client) {
    try {
      var resp = UrlFetchApp.fetch("https://api.dhan.co/v2/charts/historical", {
        method: "post",
        contentType: "application/json",
        headers: { "access-token": c.token, "client-id": c.client, "Accept": "application/json" },
        payload: JSON.stringify({ securityId: String(secId), exchangeSegment: "IDX_I", instrument: "INDEX", expiryCode: 0, oi: false, fromDate: fromDate, toDate: toDate }),
        muteHttpExceptions: true
      });
      if (resp.getResponseCode() === 200) {
        var j = JSON.parse(resp.getContentText());
        if (j && j.close && j.close.length > 20) {
          for (var i = 0; i < j.close.length; i++) {
            if (j.open[i] && j.close[i]) {
              var tsMillis = NORMALIZE_TS_MS_(j.timestamp[i]);
              candles.push({
                date: new Date(tsMillis),
                dateStr: Utilities.formatDate(new Date(tsMillis), MUMBAI_TZ, "yyyy-MM-dd"),
                open: parseFloat(j.open[i].toFixed(2)),
                high: parseFloat(j.high[i].toFixed(2)),
                low: parseFloat(j.low[i].toFixed(2)),
                close: parseFloat(j.close[i].toFixed(2))
              });
            }
          }
        }
      }
    } catch (e) { Logger.log("Dhan historical: " + e); }
  }

  if (candles.length < 20) {
    try {
      var yResp = UrlFetchApp.fetch("https://query1.finance.yahoo.com/v8/finance/chart/" + yahooSym + "?interval=1d&range=1y", {
        muteHttpExceptions: true, headers: { "User-Agent": "Mozilla/5.0" }
      });
      if (yResp.getResponseCode() === 200) {
        var res = JSON.parse(yResp.getContentText()).chart.result[0];
        var ts = res.timestamp, q = res.indicators.quote[0];
        candles = [];
        for (var k = 0; k < ts.length; k++) {
          if (q.open[k] && q.close[k] && q.high[k] && q.low[k]) {
            var dt = new Date(ts[k] * 1000);
            candles.push({
              date: dt,
              dateStr: Utilities.formatDate(dt, MUMBAI_TZ, "yyyy-MM-dd"),
              open: parseFloat(q.open[k].toFixed(2)),
              high: parseFloat(q.high[k].toFixed(2)),
              low: parseFloat(q.low[k].toFixed(2)),
              close: parseFloat(q.close[k].toFixed(2))
            });
          }
        }
      }
    } catch (e) { Logger.log("Yahoo fallback: " + e); }
  }
  return candles;
}

function AUTO_SYNC_NIFTY_GANN_LEVELS(keepDate) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var gannSheet = ss.getSheetByName(SHEET_GANN);
  if (!gannSheet) { BUILD_GANN_TAB(); gannSheet = ss.getSheetByName(SHEET_GANN); }

  var astroSheet = ss.getSheetByName(SHEET_ASTRO);
  var targetIdx = astroSheet ? astroSheet.getRange("B6").getDisplayValue() : "NIFTY 50";
  if (!targetIdx) targetIdx = "NIFTY 50";

  var candles = FETCH_HISTORICAL_DAILY_CANDLES_(targetIdx);
  if (!candles || candles.length < 20) {
    try { ss.toast("Could not fetch candles (check Dhan token / internet). Keeping last stored levels.", "⚠️ Gann sync", 8); } catch (e) {}
    return;
  }

  var now = new Date();
  var todayStr = Utilities.formatDate(now, MUMBAI_TZ, "yyyy-MM-dd");
  var sessionLive = IS_LIVE_MARKET_OPEN_NOW_();

  var lastCandle = candles[candles.length - 1];
  var d1Candle = (lastCandle.dateStr === todayStr && sessionLive && candles.length >= 2) ? candles[candles.length - 2] : lastCandle;
  var dC = d1Candle.close, dO = d1Candle.open;

  var getWeekKey = function(dt) {
    var day = parseInt(Utilities.formatDate(dt, MUMBAI_TZ, "u"), 10);
    return Utilities.formatDate(new Date(dt.getTime() - (day - 1) * 86400000), MUMBAI_TZ, "yyyy-MM-dd");
  };
  var getQtrKey = function(dt) {
    var yr = Utilities.formatDate(dt, MUMBAI_TZ, "yyyy");
    var mo = parseInt(Utilities.formatDate(dt, MUMBAI_TZ, "MM"), 10);
    return yr + "-Q" + (Math.floor((mo - 1) / 3) + 1);
  };

  var nextSessionDate = GET_ACTIVE_OR_NEXT_TRADING_DAY_();
  var nextSessionWeekKey = getWeekKey(nextSessionDate);
  var nextSessionQtrKey = getQtrKey(nextSessionDate);

  var weeksMap = {}, weekKeys = [];
  for (var i = 0; i < candles.length; i++) {
    var wk = getWeekKey(candles[i].date);
    if (!weeksMap[wk]) { weeksMap[wk] = []; weekKeys.push(wk); }
    weeksMap[wk].push(candles[i]);
  }
  var targetWeekKey = weekKeys[weekKeys.length - 1];
  if (targetWeekKey === nextSessionWeekKey && weekKeys.length >= 2) targetWeekKey = weekKeys[weekKeys.length - 2];
  var wkCandles = weeksMap[targetWeekKey] || [d1Candle];
  var wO = wkCandles[0].open, wC = wkCandles[wkCandles.length - 1].close;

  var qtrMap = {}, qtrKeys = [];
  for (var j = 0; j < candles.length; j++) {
    var qk = getQtrKey(candles[j].date);
    if (!qtrMap[qk]) { qtrMap[qk] = []; qtrKeys.push(qk); }
    qtrMap[qk].push(candles[j]);
  }
  var targetQtrKey = qtrKeys[qtrKeys.length - 1];
  if (targetQtrKey === nextSessionQtrKey && qtrKeys.length >= 2) targetQtrKey = qtrKeys[qtrKeys.length - 2];
  var qtrCandles = qtrMap[targetQtrKey];
  var qC = gannSheet.getRange("B3").getValue() || 26005.35;
  var qO = gannSheet.getRange("B4").getValue() || 25312.10;
  if (qtrCandles && qtrCandles.length >= 15) {
    qO = qtrCandles[0].open;
    qC = qtrCandles[qtrCandles.length - 1].close;
  }

  var lookback60 = candles.slice(Math.max(0, candles.length - 60));
  var eH = -Infinity, eL = Infinity;
  for (var m = 0; m < lookback60.length; m++) {
    if (lookback60[m].high > eH) eH = lookback60[m].high;
    if (lookback60[m].low < eL) eL = lookback60[m].low;
  }

  gannSheet.getRange("B3").setValue(qC);
  gannSheet.getRange("B4").setValue(qO);
  gannSheet.getRange("E3").setValue(wC);
  gannSheet.getRange("E4").setValue(wO);
  gannSheet.getRange("H3").setValue(dC);
  gannSheet.getRange("H4").setValue(dO);
  gannSheet.getRange("E22").setValue(eH);
  gannSheet.getRange("E23").setValue(eL);

  if (astroSheet && keepDate !== true) {
    astroSheet.getRange("C6").setValue(Utilities.formatDate(nextSessionDate, MUMBAI_TZ, "yyyy-MM-dd")).setNumberFormat("yyyy-mm-dd");
  }
  CLEAR_CACHE_();
  SpreadsheetApp.flush();
}

// ===================== EPHEMERIS =====================
function COMPUTE_PLANET_LON_(jd_tt, ayanamsa) {
  var d = jd_tt - 2451543.5;
  var dr = Math.PI / 180.0;
  var norm = function(x) { var r = x % 360.0; return r < 0 ? r + 360.0 : r; };

  var w_e = norm(282.9404 + 4.70935e-5 * d);
  var e_e = 0.016709 - 1.151e-9 * d;
  var M_e = norm(356.0470 + 0.9856002585 * d);
  var E_e = M_e + (180.0 / Math.PI) * e_e * Math.sin(M_e * dr) * (1.0 + e_e * Math.cos(M_e * dr));
  var xv_e = Math.cos(E_e * dr) - e_e;
  var yv_e = Math.sqrt(1.0 - e_e * e_e) * Math.sin(E_e * dr);
  var v_e = Math.atan2(yv_e, xv_e) / dr;
  var r_e = Math.sqrt(xv_e * xv_e + yv_e * yv_e);
  var lonsun = norm(v_e + w_e);
  var xs = r_e * Math.cos(lonsun * dr);
  var ys = r_e * Math.sin(lonsun * dr);

  var calcGeo = function(N, i, w, a, e, M) {
    N = norm(N); w = norm(w); M = norm(M);
    var E = M + (180.0 / Math.PI) * e * Math.sin(M * dr) * (1.0 + e * Math.cos(M * dr));
    for (var k = 0; k < 3; k++) {
      var dE = (E - (180.0 / Math.PI) * e * Math.sin(E * dr) - M) / (1.0 - e * Math.cos(E * dr));
      E -= dE;
    }
    var xv = a * (Math.cos(E * dr) - e);
    var yv = a * (Math.sqrt(1.0 - e * e) * Math.sin(E * dr));
    var v = Math.atan2(yv, xv) / dr;
    var r = Math.sqrt(xv * xv + yv * yv);
    var xh = r * (Math.cos(N * dr) * Math.cos((v + w) * dr) - Math.sin(N * dr) * Math.sin((v + w) * dr) * Math.cos(i * dr));
    var yh = r * (Math.sin(N * dr) * Math.cos((v + w) * dr) + Math.cos(N * dr) * Math.sin((v + w) * dr) * Math.cos(i * dr));
    return norm((Math.atan2(yh + ys, xh + xs) / dr) - ayanamsa);
  };

  var merc = calcGeo(48.3313 + 3.24587e-5 * d, 7.0047 + 5e-8 * d, 29.1241 + 1.01444e-5 * d, 0.387098, 0.205635 + 5.59e-10 * d, 168.6562 + 4.0923344368 * d);
  var ven  = calcGeo(76.6799 + 2.46590e-5 * d, 3.3946 + 2.75e-8 * d, 54.8910 + 1.38374e-5 * d, 0.723330, 0.006773 - 1.302e-9 * d, 48.0052 + 1.6021302244 * d);
  var mars = calcGeo(49.5574 + 2.11081e-5 * d, 1.8497 - 1.78e-8 * d, 286.5016 + 2.92961e-5 * d, 1.523688, 0.093405 + 2.516e-9 * d, 18.6021 + 0.5240207766 * d);
  var jup  = calcGeo(100.4542 + 2.76854e-5 * d, 1.3030 - 1.557e-7 * d, 273.8777 + 1.64505e-5 * d, 5.20256, 0.048498 + 4.469e-9 * d, 19.8950 + 0.0830853001 * d);
  var sat  = calcGeo(113.6634 + 2.38980e-5 * d, 2.4886 - 1.081e-7 * d, 339.3939 + 2.97661e-5 * d, 9.55475, 0.055546 - 9.499e-9 * d, 316.9670 + 0.0334442282 * d);
  var rahu = norm(125.04452 - 0.0529537648 * d - ayanamsa);
  var ketu = norm(rahu + 180.0);

  return { Mercury: merc, Venus: ven, Mars: mars, Jupiter: jup, Saturn: sat, Rahu: rahu, Ketu: ketu };
}

function COMPUTE_MUMBAI_EPHEMERIS_(dateInput, customHourIST, customMinIST) {
  var dp = DATE_PARTS_(dateInput);
  var hrIST = customHourIST !== undefined ? customHourIST : 9;
  var minIST = customMinIST !== undefined ? customMinIST : 15;

  var utcMillis = Date.UTC(dp.y, dp.mo - 1, dp.d, hrIST, minIST, 0) - (5.5 * 3600 * 1000);
  var jd = (utcMillis / 86400000.0) + 2440587.5;
  var jd_tt = jd + (69.2 / 86400.0);
  var T = (jd_tt - 2451545.0) / 36525.0;
  var dr = Math.PI / 180.0;
  var norm360 = function(x) { var r = x % 360.0; return r < 0 ? r + 360.0 : r; };

  var L0 = norm360(280.46646 + 36000.76983 * T + 0.0003032 * T * T);
  var M_sun = norm360(357.52911 + 35999.05029 * T - 0.0001537 * T * T);
  var C = (1.914602 - 0.004817 * T) * Math.sin(M_sun * dr) + 0.019993 * Math.sin(2 * M_sun * dr);
  var omega = 125.04 - 1934.136 * T;
  var sunApp = norm360(L0 + C - 0.00569 - 0.00478 * Math.sin(omega * dr));

  var Lp = norm360(218.3164477 + 481267.88123421 * T);
  var D  = norm360(297.8501921 + 445267.1114034 * T);
  var M  = norm360(357.5291092 + 35999.0502909 * T);
  var Mp = norm360(134.9633964 + 477198.8675055 * T);
  var F  = norm360(93.2720950 + 483202.0175233 * T);

  var moonCorr = 6.288774 * Math.sin(Mp * dr) + 1.274027 * Math.sin((2 * D - Mp) * dr) + 0.658314 * Math.sin(2 * D * dr) + 0.213618 * Math.sin(2 * Mp * dr) - 0.185116 * Math.sin(M * dr) - 0.114332 * Math.sin(2 * F * dr);
  var moonApp = norm360(Lp + moonCorr - 0.00478 * Math.sin(omega * dr) + 0.0075);
  var ayanamsa = 23.853056 + (jd_tt - 2451545.0) * (50.290966 / 3600.0 / 365.25);
  var sunSid = norm360(sunApp - ayanamsa);
  var moonSid = norm360(moonApp - ayanamsa);
  var elongation = norm360(moonApp - sunApp);

  var pNow = COMPUTE_PLANET_LON_(jd_tt, ayanamsa);
  var pNext = COMPUTE_PLANET_LON_(jd_tt + 0.1, ayanamsa);
  var planets = { Sun: sunSid, Moon: moonSid, Mars: pNow.Mars, Mercury: pNow.Mercury, Jupiter: pNow.Jupiter, Venus: pNow.Venus, Saturn: pNow.Saturn, Rahu: pNow.Rahu, Ketu: pNow.Ketu };

  var vakri = [];
  ["Mercury", "Venus", "Mars", "Jupiter", "Saturn"].forEach(function(k) {
    var diff = pNext[k] - pNow[k];
    if (diff > 180) diff -= 360;
    if (diff < -180) diff += 360;
    if (diff < 0) vakri.push(k + "(R)");
  });

  var elements = { fire: 0, earth: 0, air: 0, water: 0 };
  ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"].forEach(function(k) {
    var signIdx = Math.floor(planets[k] / 30) % 4;
    if (signIdx === 0) elements.fire++;
    else if (signIdx === 1) elements.earth++;
    else if (signIdx === 2) elements.air++;
    else elements.water++;
  });

  var nakshatras = ["Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra", "Punarvasu", "Pushya", "Ashlesha", "Magha", "Purva Phalguni", "Uttara Phalguni", "Hasta", "Chitra", "Swati", "Vishakha", "Anuradha", "Jyeshtha", "Mula", "Purva Ashadha", "Uttara Ashadha", "Shravana", "Dhanishta", "Shatabhisha", "Purva Bhadrapada", "Uttara Bhadrapada", "Revati"];
  var karanaIdx = Math.floor(elongation / 6.0);
  var movable = ["Bava", "Balava", "Kaulava", "Taitila", "Gara", "Vanija", "Vishti"];
  var karanaName;
  if (karanaIdx === 0) karanaName = "Kimstughna";
  else if (karanaIdx >= 57) karanaName = ["Shakuni", "Chatushpada", "Naga"][karanaIdx - 57];
  else karanaName = movable[(karanaIdx - 1) % 7];

  return {
    nakshatra: nakshatras[Math.min(26, Math.floor(moonSid / (360.0 / 27.0)))],
    elongation: elongation,
    isBhadra: (karanaName === "Vishti"),
    elements: elements,
    planets: planets,
    vakri: vakri
  };
}
