'use strict';

// ================================================================
// 工具函式
// ================================================================
const $ = id => document.getElementById(id);

function setStatus(text, badgeClass) {
  $('status-text').textContent = text;
  const badge = $('status-badge');
  badge.className = 'badge ' + badgeClass;
  const labels = {
    'badge--idle': '待機',
    'badge--drawing': '繪圖中',
    'badge--paused': '暫停',
    'badge--stopped': '已停止',
  };
  badge.textContent = labels[badgeClass] ?? text;
}

function setProgress(done, total) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  $('progress-bar').style.width = pct + '%';
  $('status-text').textContent = `${done} / ${total} 輪廓 (${pct}%)`;
}

function setButtonState(drawing) {
  $('btn-start').disabled = drawing;
  $('btn-pause').disabled = !drawing;
  $('btn-stop').disabled = !drawing;
}

// ================================================================
// 滑桿綁定（即時顯示 + 防抖預覽更新）
// ================================================================
let previewTimer = null;

function bindSlider(sliderId, displayId, transform) {
  const slider = $(sliderId);
  const display = $(displayId);
  slider.addEventListener('input', () => {
    display.textContent = transform ? transform(slider.value) : slider.value;
    schedulePreviewUpdate();
  });
}

function schedulePreviewUpdate() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(refreshPreview, 300);
}

bindSlider('thresh-low',   'val-thresh-low',   v => v);
bindSlider('thresh-high',  'val-thresh-high',  v => v);
bindSlider('min-len',      'val-min-len',       v => v);
bindSlider('avoid-left',   'val-avoid-left',   v => v);
bindSlider('avoid-right',  'val-avoid-right',  v => v);
bindSlider('avoid-top',    'val-avoid-top',    v => v);
bindSlider('avoid-bottom', 'val-avoid-bottom', v => v);
bindSlider('drag-step',    'val-drag-step',    v => v);
bindSlider('draw-delay',   'val-draw-delay',   v => (v / 100).toFixed(2));

// ================================================================
// 圖片選擇與預覽
// ================================================================
let imgNatW = 0;   // 原始圖片寬（由 API 回傳）
let imgNatH = 0;   // 原始圖片高

$('btn-open').addEventListener('click', async () => {
  const result = await window.pywebview.api.open_file_dialog();
  if (!result.ok || !result.path) return;
  $('img-path').textContent = result.path;
  await loadAndPreview(result.path);
});

async function loadAndPreview(path) {
  const resp = await window.pywebview.api.load_image(
    path,
    parseInt($('thresh-low').value),
    parseInt($('thresh-high').value),
    parseInt($('min-len').value),
  );
  if (!resp.ok) { alert('載入失敗：' + resp.error); return; }
  imgNatW = resp.img_w || 0;
  imgNatH = resp.img_h || 0;
  updatePreviewUI(resp);

  // 前景提取開啟中需重置
  if ($('fg-enable').checked) {
    $('fg-enable').checked = false;
    toggleFgMode(false);
  }
  $('btn-fg-clear').disabled = true;
}

async function refreshPreview() {
  if ($('img-path').textContent === '—') return;
  const resp = await window.pywebview.api.update_preview(
    parseInt($('thresh-low').value),
    parseInt($('thresh-high').value),
    parseInt($('min-len').value),
  );
  if (resp && resp.ok) updatePreviewUI(resp);
}

function updatePreviewUI(resp) {
  const img = $('preview-img');
  img.src = resp.preview;
  img.classList.remove('hidden');
  $('preview-placeholder').classList.add('hidden');
  $('contour-count').textContent = `輪廓數：${resp.contour_count}`;
}

// ================================================================
// 前景提取 — canvas 框選邏輯
// ================================================================
let fgMode = false;        // 是否在框選模式
let fgDragging = false;
let fgStartX = 0, fgStartY = 0;

const fgCanvas = $('fg-canvas');
const ctx = fgCanvas.getContext('2d');

/**
 * 回傳預覽圖在 canvas 中的實際渲染矩形（含 letterbox 偏移）。
 * 依據 preview-img naturalWidth/Height 計算 object-fit: contain 的邊界。
 */
function getImgRectInCanvas() {
  const box = $('preview-box');
  const boxW = box.clientWidth;
  const boxH = box.clientHeight;
  const img  = $('preview-img');
  const natW = img.naturalWidth  || boxW;
  const natH = img.naturalHeight || boxH;
  const scale = Math.min(boxW / natW, boxH / natH);
  const dispW = natW * scale;
  const dispH = natH * scale;
  return {
    left:   (boxW - dispW) / 2,
    top:    (boxH - dispH) / 2,
    width:  dispW,
    height: dispH,
    // 縮略圖 → 原始圖片的比例
    scaleX: imgNatW / natW,
    scaleY: imgNatH / natH,
  };
}

function syncCanvasSize() {
  const box = $('preview-box');
  fgCanvas.width  = box.clientWidth;
  fgCanvas.height = box.clientHeight;
}

function drawSelectionRect(x0, y0, x1, y1) {
  ctx.clearRect(0, 0, fgCanvas.width, fgCanvas.height);
  const rx = Math.min(x0, x1);
  const ry = Math.min(y0, y1);
  const rw = Math.abs(x1 - x0);
  const rh = Math.abs(y1 - y0);
  // 半透明遮罩：框外暗化
  ctx.fillStyle = 'rgba(0,0,0,0.45)';
  ctx.fillRect(0, 0, fgCanvas.width, fgCanvas.height);
  ctx.clearRect(rx, ry, rw, rh);
  // 框線
  ctx.strokeStyle = '#4f9cf9';
  ctx.lineWidth = 2;
  ctx.setLineDash([5, 3]);
  ctx.strokeRect(rx + 1, ry + 1, rw - 2, rh - 2);
  ctx.setLineDash([]);
}

fgCanvas.addEventListener('mousedown', e => {
  if (!fgMode) return;
  syncCanvasSize();
  const r = fgCanvas.getBoundingClientRect();
  fgStartX = e.clientX - r.left;
  fgStartY = e.clientY - r.top;
  fgDragging = true;
});

fgCanvas.addEventListener('mousemove', e => {
  if (!fgDragging) return;
  const r = fgCanvas.getBoundingClientRect();
  drawSelectionRect(fgStartX, fgStartY, e.clientX - r.left, e.clientY - r.top);
});

fgCanvas.addEventListener('mouseup', async e => {
  if (!fgDragging) return;
  fgDragging = false;

  const r = fgCanvas.getBoundingClientRect();
  const endX = e.clientX - r.left;
  const endY = e.clientY - r.top;

  const imgRect = getImgRectInCanvas();
  // 轉換到縮略圖座標（去掉 letterbox）
  const rx0 = (Math.min(fgStartX, endX) - imgRect.left) / imgRect.width;
  const ry0 = (Math.min(fgStartY, endY) - imgRect.top)  / imgRect.height;
  const rx1 = (Math.max(fgStartX, endX) - imgRect.left) / imgRect.width;
  const ry1 = (Math.max(fgStartY, endY) - imgRect.top)  / imgRect.height;

  // 夾緊到 [0, 1]
  const nx = Math.max(0, Math.min(rx0, 1));
  const ny = Math.max(0, Math.min(ry0, 1));
  const nw = Math.max(0, Math.min(rx1, 1)) - nx;
  const nh = Math.max(0, Math.min(ry1, 1)) - ny;

  if (nw < 0.02 || nh < 0.02) {
    ctx.clearRect(0, 0, fgCanvas.width, fgCanvas.height);
    return;
  }

  // 顯示處理中
  $('fg-status').classList.remove('hidden');
  $('fg-hint').classList.add('hidden');

  const resp = await window.pywebview.api.set_foreground_rect(nx, ny, nw, nh);
  if (!resp.ok) {
    alert('前景提取失敗：' + resp.error);
    $('fg-status').classList.add('hidden');
    $('fg-hint').classList.remove('hidden');
    ctx.clearRect(0, 0, fgCanvas.width, fgCanvas.height);
  }
  // 成功結果由 onForegroundDone 事件處理
});

/** 切換前景框選模式 */
function toggleFgMode(enable) {
  fgMode = enable;
  if (enable) {
    syncCanvasSize();
    fgCanvas.classList.remove('hidden');
    fgCanvas.classList.add('fg-active');
    // 顯示彩色原圖供框選
    window.pywebview.api.get_original_preview().then(resp => {
      if (resp.ok) {
        $('preview-img').src = resp.original_preview;
        $('preview-img').classList.remove('hidden');
        $('preview-placeholder').classList.add('hidden');
        imgNatW = resp.img_w;
        imgNatH = resp.img_h;
      }
    });
    $('fg-hint').classList.remove('hidden');
  } else {
    fgCanvas.classList.add('hidden');
    fgCanvas.classList.remove('fg-active');
    ctx.clearRect(0, 0, fgCanvas.width, fgCanvas.height);
    $('fg-hint').classList.add('hidden');
    $('fg-status').classList.add('hidden');
    // 恢復邊緣預覽
    refreshPreview();
  }
}

$('fg-enable').addEventListener('change', e => {
  if ($('img-path').textContent === '—') {
    e.target.checked = false;
    return;
  }
  toggleFgMode(e.target.checked);
});

$('btn-fg-clear').addEventListener('click', async () => {
  const resp = await window.pywebview.api.clear_foreground();
  if (resp.ok) {
    updatePreviewUI(resp);
    $('btn-fg-clear').disabled = true;
    if ($('fg-enable').checked) {
      $('fg-enable').checked = false;
      toggleFgMode(false);
    }
  }
});

window.addEventListener('onForegroundDone', e => {
  $('fg-status').classList.add('hidden');
  updatePreviewUI(e.detail);
  $('btn-fg-clear').disabled = false;
  // 框選完成後退出框選模式，顯示邊緣預覽
  $('fg-enable').checked = false;
  toggleFgMode(false);
});

window.addEventListener('onForegroundError', e => {
  $('fg-status').classList.add('hidden');
  $('fg-hint').classList.remove('hidden');
  alert('前景提取失敗：' + e.detail.error);
  ctx.clearRect(0, 0, fgCanvas.width, fgCanvas.height);
});

// ================================================================
// 繪製起點選取
// ================================================================
let anchorX = null;
let anchorY = null;
let pickingActive = false;

$('use-anchor').addEventListener('change', e => {
  const section = $('anchor-section');
  if (e.target.checked) {
    section.classList.remove('hidden');
  } else {
    section.classList.add('hidden');
    anchorX = null;
    anchorY = null;
  }
});

$('btn-pick-pos').addEventListener('click', async () => {
  if (pickingActive) return;
  pickingActive = true;
  $('btn-pick-pos').disabled = true;
  $('pick-countdown').textContent = '...';

  const resp = await window.pywebview.api.start_pick_position(3);
  if (!resp.ok) {
    pickingActive = false;
    $('btn-pick-pos').disabled = false;
    $('pick-countdown').textContent = '';
  }
  // 結果由 onPickCountdown / onPickDone 事件推送
});

window.addEventListener('onPickCountdown', e => {
  $('pick-countdown').textContent = e.detail.remaining;
});

window.addEventListener('onPickDone', e => {
  anchorX = e.detail.x;
  anchorY = e.detail.y;
  $('anchor-x').value = anchorX;
  $('anchor-y').value = anchorY;
  $('pick-countdown').textContent = '✓';
  setTimeout(() => { $('pick-countdown').textContent = ''; }, 1500);
  pickingActive = false;
  $('btn-pick-pos').disabled = false;
});

// 手動修改 X/Y 輸入框也更新 anchor
$('anchor-x').addEventListener('change', () => { anchorX = parseInt($('anchor-x').value) || null; });
$('anchor-y').addEventListener('change', () => { anchorY = parseInt($('anchor-y').value) || null; });

// ================================================================
// 繪圖控制按鈕
// ================================================================
$('btn-start').addEventListener('click', async () => {
  const params = gatherParams();
  const resp = await window.pywebview.api.start_drawing(params);
  if (!resp.ok) { alert('啟動失敗：' + resp.error); return; }
  setStatus('繪圖中', 'badge--drawing');
  setButtonState(true);
  $('progress-bar').style.width = '0%';
});

$('btn-pause').addEventListener('click', async () => {
  const state = await window.pywebview.api.get_state();
  if (state === 'DRAWING') {
    await window.pywebview.api.pause_drawing();
    $('btn-pause').textContent = '▶ 繼續 (F9)';
    setStatus('已暫停', 'badge--paused');
  } else if (state === 'PAUSED') {
    const autoAlign = $('auto-align').checked;
    await window.pywebview.api.resume_drawing(autoAlign);
    $('btn-pause').textContent = '⏸ 暫停 (F9)';
    setStatus('繪圖中', 'badge--drawing');
  }
});

$('btn-stop').addEventListener('click', async () => {
  await window.pywebview.api.stop_drawing();
  resetUI();
});

// ================================================================
// 熱鍵套用
// ================================================================
$('btn-apply-keys').addEventListener('click', async () => {
  const ks = $('key-start').value.trim();
  const kp = $('key-pause').value.trim();
  const kt = $('key-stop').value.trim();
  const resp = await window.pywebview.api.update_hotkeys(ks, kp, kt);
  if (resp.ok) {
    alert(`熱鍵已更新：開始=${resp.keys.start}  暫停=${resp.keys.pause}  停止=${resp.keys.stop}`);
  }
});

// ================================================================
// 收集所有參數
// ================================================================
function gatherParams() {
  const params = {
    avoid_left:   parseInt($('avoid-left').value) / 100,
    avoid_right:  parseInt($('avoid-right').value) / 100,
    avoid_top:    parseInt($('avoid-top').value) / 100,
    avoid_bottom: parseInt($('avoid-bottom').value) / 100,
    drag_step:    parseInt($('drag-step').value),
    draw_delay:   parseInt($('draw-delay').value) / 100,
    draw_button:  $('draw-button').value,
  };
  if ($('use-anchor').checked && anchorX !== null && anchorY !== null) {
    params.anchor_x = anchorX;
    params.anchor_y = anchorY;
  }
  return params;
}

// ================================================================
// 重置 UI
// ================================================================
function resetUI() {
  setStatus('待機', 'badge--idle');
  setButtonState(false);
  $('btn-pause').textContent = '⏸ 暫停 (F9)';
  $('progress-bar').style.width = '0%';
}

// ================================================================
// 後端事件監聽
// ================================================================
window.addEventListener('onProgress', e => {
  setProgress(e.detail.done, e.detail.total);
});

window.addEventListener('onDrawingFinished', () => {
  resetUI();
  $('status-text').textContent = '繪圖完成';
  $('progress-bar').style.width = '100%';
});

window.addEventListener('onHotkeyStart', () => {
  $('btn-start').click();
});

window.addEventListener('onHotkeyPause', () => {
  $('btn-pause').textContent = '▶ 繼續 (F9)';
  setStatus('已暫停', 'badge--paused');
});

window.addEventListener('onHotkeyResume', () => {
  $('btn-pause').textContent = '⏸ 暫停 (F9)';
  setStatus('繪圖中', 'badge--drawing');
});

window.addEventListener('onHotkeyStop', () => {
  resetUI();
});

window.addEventListener('onAlignFailed', () => {
  console.warn('自動對齊失敗，繼續以原座標繪圖');
});
