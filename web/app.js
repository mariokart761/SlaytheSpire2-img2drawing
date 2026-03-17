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
    'badge--idle':    '待機',
    'badge--drawing': '繪圖中',
    'badge--paused':  '暫停',
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
  $('btn-stop').disabled  = !drawing;
}

// ================================================================
// 通用「checkbox 展開/收合 sub-params」工具
// ================================================================
function bindToggle(checkboxId, subParamsId) {
  const cb  = $(checkboxId);
  const sub = $(subParamsId);
  cb.addEventListener('change', () => {
    sub.classList.toggle('hidden', !cb.checked);
    schedulePreviewUpdate();
  });
}

bindToggle('use-clahe',          'clahe-params');
bindToggle('use-bilateral',      'bilateral-params');
bindToggle('use-median',         'median-params');
bindToggle('use-morph-close',    'morph-close-params');
bindToggle('use-morph-open',     'morph-open-params');
bindToggle('use-region-filter',  'region-filter-params');
bindToggle('use-approx',         'approx-params');

// ================================================================
// 滑桿綁定（即時顯示 + 防抖預覽更新）
// ================================================================
let previewTimer = null;

function bindSlider(sliderId, displayId, transform, noPreview) {
  const slider  = $(sliderId);
  const display = $(displayId);
  slider.addEventListener('input', () => {
    display.textContent = transform ? transform(slider.value) : slider.value;
    if (!noPreview) schedulePreviewUpdate();
  });
}

function schedulePreviewUpdate() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(refreshPreview, 350);
}

// 邊緣偵測
bindSlider('thresh-low',          'val-thresh-low',          v => v);
bindSlider('thresh-high',         'val-thresh-high',         v => v);
// 前處理：CLAHE
bindSlider('clahe-clip',          'val-clahe-clip',          v => (v / 10).toFixed(1));
// 前處理：bilateral / median
bindSlider('bilateral-sigma',     'val-bilateral-sigma',     v => v);
bindSlider('median-ksize',        'val-median-ksize',        v => v);
// 形態學
bindSlider('morph-close-ksize',   'val-morph-close-ksize',   v => v);
bindSlider('morph-open-ksize',    'val-morph-open-ksize',    v => v);
// 分區域過濾
bindSlider('region-split',        'val-region-split',        v => v);
bindSlider('region-sigma',        'val-region-sigma',        v => v);
bindSlider('region-thresh-low',   'val-region-thresh-low',   v => v);
bindSlider('region-thresh-high',  'val-region-thresh-high',  v => v);
// 輪廓過濾
bindSlider('min-len',             'val-min-len',             v => v);
bindSlider('min-area',            'val-min-area',            v => v);
bindSlider('approx-eps',          'val-approx-eps',          v => v);
// 繪圖（不觸發預覽更新）
bindSlider('avoid-left',          'val-avoid-left',          v => v,    true);
bindSlider('avoid-right',         'val-avoid-right',         v => v,    true);
bindSlider('avoid-top',           'val-avoid-top',           v => v,    true);
bindSlider('avoid-bottom',        'val-avoid-bottom',        v => v,    true);
bindSlider('drag-step',           'val-drag-step',           v => v,    true);
bindSlider('draw-delay',          'val-draw-delay',          v => (v / 100).toFixed(2), true);

// ================================================================
// 收集所有影像處理參數（交給 update_preview / load_image）
// ================================================================
function gatherProcessingParams() {
  return {
    // Canny
    threshold_low:            parseInt($('thresh-low').value),
    threshold_high:           parseInt($('thresh-high').value),
    // CLAHE
    use_clahe:                $('use-clahe').checked,
    clahe_clip:               parseInt($('clahe-clip').value) / 10,
    // Bilateral / Median
    use_bilateral:            $('use-bilateral').checked,
    bilateral_sigma:          parseInt($('bilateral-sigma').value),
    use_median:               $('use-median').checked,
    median_ksize:             parseInt($('median-ksize').value),
    // Morphology
    use_morph_close:          $('use-morph-close').checked,
    morph_close_ksize:        parseInt($('morph-close-ksize').value),
    use_morph_open:           $('use-morph-open').checked,
    morph_open_ksize:         parseInt($('morph-open-ksize').value),
    // Thinning
    use_thinning:             $('use-thinning').checked,
    // Region filter
    use_region_filter:        $('use-region-filter').checked,
    region_split_pct:         parseInt($('region-split').value),
    region_lower_sigma:       parseInt($('region-sigma').value),
    region_lower_thresh_low:  parseInt($('region-thresh-low').value),
    region_lower_thresh_high: parseInt($('region-thresh-high').value),
    // Contour filter
    min_length:               parseInt($('min-len').value),
    min_area:                 parseInt($('min-area').value),
    // D-P
    use_approx:               $('use-approx').checked,
    approx_epsilon_ppm:       parseInt($('approx-eps').value),
  };
}

// 收集繪圖執行參數
function gatherDrawParams() {
  const params = {
    avoid_left:   parseInt($('avoid-left').value)  / 100,
    avoid_right:  parseInt($('avoid-right').value) / 100,
    avoid_top:    parseInt($('avoid-top').value)   / 100,
    avoid_bottom: parseInt($('avoid-bottom').value)/ 100,
    drag_step:    parseInt($('drag-step').value),
    draw_delay:   parseInt($('draw-delay').value)  / 100,
    draw_button:  $('draw-button').value,
  };
  if ($('use-anchor').checked && anchorX !== null && anchorY !== null) {
    params.anchor_x = anchorX;
    params.anchor_y = anchorY;
  }
  return params;
}

// ================================================================
// 圖片選擇與預覽
// ================================================================
let imgNatW = 0;
let imgNatH = 0;

$('btn-open').addEventListener('click', async () => {
  const result = await window.pywebview.api.open_file_dialog();
  if (!result.ok || !result.path) return;
  $('img-path').textContent = result.path;
  await loadAndPreview(result.path);
});

async function loadAndPreview(path) {
  const resp = await window.pywebview.api.load_image(path, gatherProcessingParams());
  if (!resp.ok) { alert('載入失敗：' + resp.error); return; }
  imgNatW = resp.img_w || 0;
  imgNatH = resp.img_h || 0;
  updatePreviewUI(resp);

  if ($('fg-enable').checked) {
    $('fg-enable').checked = false;
    toggleFgMode(false);
  }
  $('btn-fg-clear').disabled = true;
}

async function refreshPreview() {
  if ($('img-path').textContent === '—') return;
  const resp = await window.pywebview.api.update_preview(gatherProcessingParams());
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
let fgMode     = false;
let fgDragging = false;
let fgStartX   = 0, fgStartY = 0;

const fgCanvas = $('fg-canvas');
const ctx      = fgCanvas.getContext('2d');

function getImgRectInCanvas() {
  const box  = $('preview-box');
  const boxW = box.clientWidth;
  const boxH = box.clientHeight;
  const img  = $('preview-img');
  const natW = img.naturalWidth  || boxW;
  const natH = img.naturalHeight || boxH;
  const scale = Math.min(boxW / natW, boxH / natH);
  return {
    left:   (boxW - natW * scale) / 2,
    top:    (boxH - natH * scale) / 2,
    width:   natW * scale,
    height:  natH * scale,
  };
}

function syncCanvasSize() {
  const box = $('preview-box');
  fgCanvas.width  = box.clientWidth;
  fgCanvas.height = box.clientHeight;
}

function drawSelectionRect(x0, y0, x1, y1) {
  ctx.clearRect(0, 0, fgCanvas.width, fgCanvas.height);
  const rx = Math.min(x0, x1), ry = Math.min(y0, y1);
  const rw = Math.abs(x1 - x0), rh = Math.abs(y1 - y0);
  ctx.fillStyle = 'rgba(0,0,0,0.45)';
  ctx.fillRect(0, 0, fgCanvas.width, fgCanvas.height);
  ctx.clearRect(rx, ry, rw, rh);
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

  const r   = fgCanvas.getBoundingClientRect();
  const endX = e.clientX - r.left;
  const endY = e.clientY - r.top;

  const ir  = getImgRectInCanvas();
  const nx  = Math.max(0, Math.min((Math.min(fgStartX, endX) - ir.left) / ir.width, 1));
  const ny  = Math.max(0, Math.min((Math.min(fgStartY, endY) - ir.top)  / ir.height, 1));
  const nx2 = Math.max(0, Math.min((Math.max(fgStartX, endX) - ir.left) / ir.width, 1));
  const ny2 = Math.max(0, Math.min((Math.max(fgStartY, endY) - ir.top)  / ir.height, 1));

  if (nx2 - nx < 0.02 || ny2 - ny < 0.02) {
    ctx.clearRect(0, 0, fgCanvas.width, fgCanvas.height);
    return;
  }

  $('fg-status').classList.remove('hidden');
  $('fg-hint').classList.add('hidden');

  const resp = await window.pywebview.api.set_foreground_rect(nx, ny, nx2 - nx, ny2 - ny);
  if (!resp.ok) {
    alert('前景提取失敗：' + resp.error);
    $('fg-status').classList.add('hidden');
    $('fg-hint').classList.remove('hidden');
    ctx.clearRect(0, 0, fgCanvas.width, fgCanvas.height);
  }
});

function toggleFgMode(enable) {
  fgMode = enable;
  if (enable) {
    syncCanvasSize();
    fgCanvas.classList.remove('hidden');
    fgCanvas.classList.add('fg-active');
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
    refreshPreview();
  }
}

$('fg-enable').addEventListener('change', e => {
  if ($('img-path').textContent === '—') { e.target.checked = false; return; }
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
let anchorX      = null;
let anchorY      = null;
let pickingActive = false;

$('use-anchor').addEventListener('change', e => {
  $('anchor-section').classList.toggle('hidden', !e.target.checked);
  if (!e.target.checked) { anchorX = null; anchorY = null; }
});

$('btn-pick-pos').addEventListener('click', async () => {
  if (pickingActive) return;
  pickingActive = true;
  $('btn-pick-pos').disabled = true;
  $('pick-countdown').textContent = '...';
  await window.pywebview.api.start_pick_position(3);
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

$('anchor-x').addEventListener('change', () => { anchorX = parseInt($('anchor-x').value) || null; });
$('anchor-y').addEventListener('change', () => { anchorY = parseInt($('anchor-y').value) || null; });

// ================================================================
// 繪圖控制按鈕
// ================================================================
$('btn-start').addEventListener('click', async () => {
  const params = gatherDrawParams();
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
    await window.pywebview.api.resume_drawing($('auto-align').checked);
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
  const resp = await window.pywebview.api.update_hotkeys(
    $('key-start').value.trim(),
    $('key-pause').value.trim(),
    $('key-stop').value.trim(),
  );
  if (resp.ok) {
    alert(`熱鍵已更新：開始=${resp.keys.start}  暫停=${resp.keys.pause}  停止=${resp.keys.stop}`);
  }
});

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

window.addEventListener('onHotkeyStart',  () => { $('btn-start').click(); });
window.addEventListener('onHotkeyPause',  () => {
  $('btn-pause').textContent = '▶ 繼續 (F9)';
  setStatus('已暫停', 'badge--paused');
});
window.addEventListener('onHotkeyResume', () => {
  $('btn-pause').textContent = '⏸ 暫停 (F9)';
  setStatus('繪圖中', 'badge--drawing');
});
window.addEventListener('onHotkeyStop',   () => { resetUI(); });
window.addEventListener('onAlignFailed',  () => {
  console.warn('自動對齊失敗，繼續以原座標繪圖');
});
