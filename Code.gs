/** @OnlyCurrentDoc */
// ↑ この行があると、GAS が触れるのは「このスプレッドシートだけ」に限定される（安全のため消さないこと）

/**
 * NFC 収穫管理システム － Google Apps Script（GAS）
 *
 * ・index.html から location.href で送られてきた記録をスプレッドシートに保存し、
 *   「記録しました」画面を表示します。
 * ・dashboard.html から呼ばれたときは、その日の記録をデータとして返します。
 */

// ====== 設定 ======================================================
// スプレッドシートから「拡張機能 → Apps Script」で作った場合は空のままでOK。
// 別に作ったスクリプトの場合は、スプレッドシートURLの /d/ と /edit の間の文字列を入れる。
// （その場合は1行目の @OnlyCurrentDoc の行を消す必要がある）
const SPREADSHEET_ID = '';
const SHEET_NAME = '記録';
const TZ = 'Asia/Tokyo';
// 合言葉。iPhone とダッシュボードから送られてくる合言葉がこれと一致しないと、記録も閲覧もできない。
// （GitHub には載せないこと）
const ACCESS_KEY = 'ここに合言葉';
// 記録完了画面の「戻る」ボタンの行き先
const BACK_URL = 'https://theyasai.github.io/harvest/';
// 同じコンテナを何秒以内に再タッチしたら「二重タッチ」とみなして記録しないか
const DOUBLE_TOUCH_SECONDS = 60;
// 重複チェックや本日件数の計算で見る直近の行数
const RECENT_ROWS = 5000;
// ================================================================

const HEADERS = ['日時', '作業者', 'コンテナID', '端末', '受信日時', '記録ID'];

function doGet(e) {
  const p = (e && e.parameter) || {};
  const keyOk = ACCESS_KEY && ACCESS_KEY !== 'ここに合言葉' && p.key === ACCESS_KEY;
  if (p.action === 'record') {
    if (!keyOk) return page_('ng', '記録できませんでした', '合言葉が違います。管理者に「設定用リンク」をもらってください', '', '');
    return record_(p);
  }
  if (p.action === 'data' && !keyOk) return jsonOut_(p, { error: 'key' });
  if (p.action === 'data') return data_(p);
  return ContentService.createTextOutput('harvest GAS OK');
}

// ---------------------------------------------------------------
// シートの取得（無ければ作る）
// ---------------------------------------------------------------
function getSheet_() {
  const ss = SPREADSHEET_ID
    ? SpreadsheetApp.openById(SPREADSHEET_ID)
    : SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(SHEET_NAME);
  if (!sh) {
    sh = ss.insertSheet(SHEET_NAME);
    sh.appendRow(HEADERS);
    sh.setFrozenRows(1);
    sh.getRange('A:A').setNumberFormat('yyyy/mm/dd hh:mm:ss');
    sh.getRange('E:E').setNumberFormat('yyyy/mm/dd hh:mm:ss');
    sh.getRange('C:D').setNumberFormat('@'); // 0001 の先頭の0が消えないように文字列扱い
  }
  return sh;
}

// 直近の行を取得
function recentRows_(sh) {
  const last = sh.getLastRow();
  if (last < 2) return [];
  const n = Math.min(RECENT_ROWS, last - 1);
  return sh.getRange(last - n + 1, 1, n, HEADERS.length).getValues();
}

function pad_(v, len) {
  return String(v).trim().padStart(len, '0');
}

function dayStr_(d) {
  return Utilities.formatDate(d, TZ, 'yyyy-MM-dd');
}

// ---------------------------------------------------------------
// 記録（index.html から）
// ---------------------------------------------------------------
function record_(p) {
  const worker = String(p.worker || '').trim().slice(0, 30);
  const containerRaw = String(p.container || '').trim();
  const deviceRaw = String(p.device || '').trim();
  const rid = String(p.rid || '').trim().slice(0, 40);

  if (!worker || !/^\d{1,4}$/.test(containerRaw) || !/^\d{1,2}$/.test(deviceRaw)) {
    return page_('ng', '記録できませんでした', '送信内容が正しくありません', '', '');
  }
  const container = pad_(containerRaw, 4);
  const device = pad_(deviceRaw, 2);

  // タッチ時刻（iPhone の時計が大きくずれている場合はサーバー時刻を使う）
  const now = new Date();
  const t = Number(p.t);
  const touched = (t && Math.abs(now.getTime() - t) < 24 * 3600 * 1000) ? new Date(t) : now;

  // 5台から同時に送られても行が混ざらないようにロックする
  const lock = LockService.getScriptLock();
  try {
    lock.waitLock(20000);
  } catch (err) {
    return page_('ng', '混み合っています', 'もう一度コンテナをタッチしてください', 'No.' + container, worker);
  }

  try {
    const sh = getSheet_();
    const rows = recentRows_(sh);
    const today = dayStr_(touched);

    let status = 'ok';
    for (let i = rows.length - 1; i >= 0; i--) {
      const r = rows[i];
      // 同じ記録ID（画面の再読み込みなど）
      if (rid && r[5] === rid) { status = 'dup'; break; }
      // 同じコンテナを短時間に再タッチ
      if (r[0] instanceof Date && pad_(r[2], 4) === container &&
          Math.abs(touched.getTime() - r[0].getTime()) < DOUBLE_TOUCH_SECONDS * 1000) {
        status = 'dup';
        break;
      }
    }

    if (status === 'ok') {
      // 先頭に ' を付けると 0001 が数値の 1 にならず文字列のまま保存される
      sh.appendRow([touched, worker, "'" + container, "'" + device, now, rid]);
      rows.push([touched, worker, container, device, now, rid]);
    }

    // この作業者の本日の件数
    let count = 0;
    rows.forEach(function (r) {
      if (r[0] instanceof Date && r[1] === worker && dayStr_(r[0]) === today) count++;
    });

    const timeStr = Utilities.formatDate(touched, TZ, 'HH:mm:ss');
    if (status === 'dup') {
      return page_('warn', '記録済みです', '二重タッチのため今回は記録していません', 'No.' + container,
        worker + ' さん ／ 本日 ' + count + ' ケース');
    }
    return page_('ok', '記録しました', timeStr + ' ／ 端末' + device, 'No.' + container,
      worker + ' さん ／ 本日 ' + count + ' ケース目');
  } finally {
    lock.releaseLock();
  }
}

// 記録結果の画面
function page_(type, title, sub, big, detail) {
  const color = { ok: '#1f8a3b', warn: '#c77700', ng: '#c62828' }[type];
  const icon = { ok: '✅', warn: '⚠️', ng: '❌' }[type];
  const t = HtmlService.createTemplate(
    '<!DOCTYPE html><html><head><meta charset="UTF-8">' +
    '<style>' +
    'body{margin:0;font-family:-apple-system,"Hiragino Sans",sans-serif;background:#f4f6f2;color:#1d1f1b;padding:16px;}' +
    '.card{max-width:520px;margin:0 auto;background:#fff;border:6px solid <?= color ?>;border-radius:20px;padding:28px 16px;text-align:center;}' +
    '.icon{font-size:72px;line-height:1;}' +
    'h1{font-size:30px;margin:12px 0 4px;color:<?= color ?>;}' +
    '.sub{font-size:16px;color:#5b6157;}' +
    '.big{font-size:52px;font-weight:800;margin:16px 0 8px;}' +
    '.detail{font-size:22px;font-weight:700;}' +
    'a{display:block;max-width:520px;margin:16px auto 0;padding:16px 0;border-radius:12px;background:#fff;border:1px solid #dde2d8;' +
    'color:#1d1f1b;text-align:center;font-size:18px;font-weight:700;text-decoration:none;}' +
    '</style></head><body>' +
    '<div class="card"><div class="icon"><?= icon ?></div><h1><?= title ?></h1>' +
    '<div class="sub"><?= sub ?></div><div class="big"><?= big ?></div>' +
    '<div class="detail"><?= detail ?></div></div>' +
    '<a href="<?= backUrl ?>" target="_top">ホームに戻る</a>' +
    '</body></html>'
  );
  t.color = color; t.icon = icon; t.title = title; t.sub = sub;
  t.big = big; t.detail = detail; t.backUrl = BACK_URL;
  return t.evaluate()
    .setTitle(title)
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

// ---------------------------------------------------------------
// データ取得（dashboard.html から）
// ---------------------------------------------------------------
function data_(p) {
  const date = /^\d{4}-\d{2}-\d{2}$/.test(p.date || '') ? p.date : dayStr_(new Date());
  const sh = getSheet_();
  const last = sh.getLastRow();
  const out = [];
  if (last >= 2) {
    const values = sh.getRange(2, 1, last - 1, 4).getValues();
    values.forEach(function (r) {
      if (!(r[0] instanceof Date) || dayStr_(r[0]) !== date) return;
      out.push([
        Utilities.formatDate(r[0], TZ, 'HH:mm:ss'),
        String(r[1]),
        pad_(r[2], 4),
        pad_(r[3], 2)
      ]);
    });
  }
  out.sort(function (a, b) { return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0; });

  return jsonOut_(p, {
    date: date,
    updated: Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd HH:mm:ss'),
    rows: out // [時刻, 作業者, コンテナID, 端末]
  });
}

function jsonOut_(p, obj) {
  const json = JSON.stringify(obj);
  // JSONP（<script> タグで読み込む方式。CORS の制限を受けない）
  const cb = String(p.callback || '');
  if (/^[A-Za-z_$][\w$]{0,50}$/.test(cb)) {
    return ContentService.createTextOutput(cb + '(' + json + ');')
      .setMimeType(ContentService.MimeType.JAVASCRIPT);
  }
  return ContentService.createTextOutput(json).setMimeType(ContentService.MimeType.JSON);
}
