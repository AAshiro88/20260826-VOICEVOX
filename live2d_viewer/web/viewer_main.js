/**
 * Live2D 檢視器前端。（esbuild 入口：web/viewer_main.js 捆成 web/viewer.bundle.js）
 *
 * 功能：
 * - 載入 /api/models 清單並用下拉切換模型
 * - 每約 1 秒以長時間輪詢 /api/poll 取得控制指令（[3d] JSON）
 * - 依指令執行表情／動作／參數演出，並支援簡單口型同步
 */

import { CubismFramework } from '../../CubismSdkForWeb-5-r.5/Framework/src/live2dcubismframework.ts';
import { CubismDefaultParameterId } from '../../CubismSdkForWeb-5-r.5/Framework/src/cubismdefaultparameterid.ts';
import { CubismModelSettingJson } from '../../CubismSdkForWeb-5-r.5/Framework/src/cubismmodelsettingjson.ts';
import { CubismUserModel } from '../../CubismSdkForWeb-5-r.5/Framework/src/model/cubismusermodel.ts';
import { CubismMatrix44 } from '../../CubismSdkForWeb-5-r.5/Framework/src/math/cubismmatrix44.ts';
import { CubismEyeBlink } from '../../CubismSdkForWeb-5-r.5/Framework/src/effect/cubismeyeblink.ts';
import { CubismBreath, BreathParameterData } from '../../CubismSdkForWeb-5-r.5/Framework/src/effect/cubismbreath.ts';
import { CubismLook, LookParameterData } from '../../CubismSdkForWeb-5-r.5/Framework/src/effect/cubismlook.ts';

const P = CubismDefaultParameterId;

// ---------------------------------------------------------------------------
// 參數 ID 快取（避免每幀重複尋找）
// ---------------------------------------------------------------------------
const pidCache = {};
function pid(name) {
  if (!pidCache[name]) {
    pidCache[name] = CubismFramework.getIdManager().getId(name);
  }
  return pidCache[name];
}

// ---------------------------------------------------------------------------
// 表情預設表：paramName → 目標值（0..1 或實際座標域）
// ---------------------------------------------------------------------------
// 部分第三方模型以「零件開關」呈現害羞、生氣等表情（物理無法覆寫開關層）。
// 具備這些參數的模型會直接開啟對應開關；其他模型沒有這些參數，套用時自動忽略。
const SWITCH_PARAMS = ['Param91', 'Param92', 'Param93', 'Param94'];
const SWITCH_RESET = {};
for (const s of SWITCH_PARAMS) SWITCH_RESET[s] = 0;

// 切換表情時需要歸零的標準表情參數（一次性套用，之後交給物理自然接管）。
// 不包含眼皮（避免干擾眨眼）與頭部／眼球角度（避免干擾視線跟隨、動作）。
const FACIAL_RESET = {
  [P.ParamBrowLY]: 0,
  [P.ParamBrowRY]: 0,
  [P.ParamBrowLAngle]: 0,
  [P.ParamBrowRAngle]: 0,
  [P.ParamEyeLSmile]: 0,
  [P.ParamEyeRSmile]: 0,
  [P.ParamMouthForm]: 0,
  [P.ParamMouthOpenY]: 0,
};

const EXPRESSIONS = {
  neutral: {},
  happy: {
    [P.ParamBrowLY]: 0.5,
    [P.ParamBrowRY]: 0.5,
    [P.ParamMouthForm]: 1.0,
    [P.ParamMouthOpenY]: 0.15,
    [P.ParamEyeLSmile]: 0.6,
    [P.ParamEyeRSmile]: 0.6,
  },
  angry: {
    'Param104': 1.0,
    'Param92': 1.0,
    [P.ParamBrowLY]: -0.4,
    [P.ParamBrowRY]: -0.4,
    [P.ParamMouthForm]: 0.4,
    [P.ParamMouthOpenY]: 0.2,
  },
  sad: {
    [P.ParamBrowLY]: -0.5,
    [P.ParamBrowRY]: -0.5,
    [P.ParamMouthForm]: -0.6,
    [P.ParamEyeLOpen]: 0.6,
    [P.ParamEyeROpen]: 0.6,
    'Param130': 0.3,
  },
  surprised: {
    [P.ParamBrowLY]: 0.8,
    [P.ParamBrowRY]: 0.8,
    [P.ParamEyeLOpen]: 1.0,
    [P.ParamEyeROpen]: 1.0,
    [P.ParamMouthOpenY]: 0.9,
    [P.ParamMouthForm]: 0.2,
  },
  love: {
    'Param109': 1.0,
    [P.ParamBrowLY]: 0.2,
    [P.ParamBrowRY]: 0.2,
    [P.ParamMouthForm]: 0.6,
    [P.ParamEyeLSmile]: 0.8,
    [P.ParamEyeRSmile]: 0.8,
  },
  cry: {
    'Param130': 1.0,
    [P.ParamBrowLY]: -0.5,
    [P.ParamBrowRY]: -0.5,
    [P.ParamMouthForm]: -0.5,
    [P.ParamMouthOpenY]: 0.4,
    [P.ParamEyeLOpen]: 0.5,
    [P.ParamEyeROpen]: 0.5,
  },
  blush: {
    'Param91': 1.0,
    [P.ParamBrowLY]: 0.2,
    [P.ParamBrowRY]: 0.2,
    [P.ParamMouthForm]: 0.3,
    [P.ParamEyeLSmile]: 0.4,
    [P.ParamEyeRSmile]: 0.4,
  },
  sleep: {
    [P.ParamEyeLOpen]: 0.0,
    [P.ParamEyeROpen]: 0.0,
    [P.ParamMouthForm]: 0.0,
    [P.ParamBrowLY]: 0.0,
    [P.ParamBrowRY]: 0.0,
  },
  worried: {
    [P.ParamBrowLY]: -0.4,
    [P.ParamBrowRY]: -0.4,
    [P.ParamBrowLAngle]: 0.5,
    [P.ParamBrowRAngle]: 0.5,
    [P.ParamEyeLOpen]: 0.6,
    [P.ParamEyeROpen]: 0.6,
    [P.ParamMouthForm]: 0.1,
  },
  smug: {
    'Param93': 1.0,
    [P.ParamBrowLY]: 0.3,
    [P.ParamBrowRY]: 0.0,
    [P.ParamMouthForm]: 0.7,
    [P.ParamEyeLOpen]: 0.5,
    [P.ParamEyeLSmile]: 0.4,
  },
};

// ---------------------------------------------------------------------------
// 動作預設表：keyframes [{t 秒, p {參數: 值}}]
// ---------------------------------------------------------------------------
const MOTIONS = {
  nod: {
    dur: 1.1,
    keys: [
      { t: 0.0, p: { [P.ParamAngleZ]: 0 } },
      { t: 0.25, p: { [P.ParamAngleZ]: 12 } },
      { t: 0.55, p: { [P.ParamAngleZ]: -10 } },
      { t: 1.0, p: { [P.ParamAngleZ]: 0 } },
    ],
  },
  shake: {
    dur: 1.0,
    keys: [
      { t: 0.0, p: { [P.ParamAngleY]: 0 } },
      { t: 0.2, p: { [P.ParamAngleY]: -14 } },
      { t: 0.45, p: { [P.ParamAngleY]: 14 } },
      { t: 0.7, p: { [P.ParamAngleY]: -10 } },
      { t: 0.95, p: { [P.ParamAngleY]: 0 } },
    ],
  },
  look_left: {
    dur: 1.6,
    keys: [
      { t: 0.0, p: { [P.ParamAngleY]: 0, [P.ParamEyeBallX]: 0 } },
      { t: 0.4, p: { [P.ParamAngleY]: 18, [P.ParamEyeBallX]: 0.8 } },
      { t: 1.2, p: { [P.ParamAngleY]: 18, [P.ParamEyeBallX]: 0.8 } },
      { t: 1.6, p: { [P.ParamAngleY]: 0, [P.ParamEyeBallX]: 0 } },
    ],
  },
  look_right: {
    dur: 1.6,
    keys: [
      { t: 0.0, p: { [P.ParamAngleY]: 0, [P.ParamEyeBallX]: 0 } },
      { t: 0.4, p: { [P.ParamAngleY]: -18, [P.ParamEyeBallX]: -0.8 } },
      { t: 1.2, p: { [P.ParamAngleY]: -18, [P.ParamEyeBallX]: -0.8 } },
      { t: 1.6, p: { [P.ParamAngleY]: 0, [P.ParamEyeBallX]: 0 } },
    ],
  },
  look_up: {
    dur: 1.6,
    keys: [
      { t: 0.0, p: { [P.ParamAngleZ]: 0, [P.ParamEyeBallY]: 0 } },
      { t: 0.4, p: { [P.ParamAngleZ]: 14, [P.ParamEyeBallY]: 0.8 } },
      { t: 1.2, p: { [P.ParamAngleZ]: 14, [P.ParamEyeBallY]: 0.8 } },
      { t: 1.6, p: { [P.ParamAngleZ]: 0, [P.ParamEyeBallY]: 0 } },
    ],
  },
  look_down: {
    dur: 1.6,
    keys: [
      { t: 0.0, p: { [P.ParamAngleZ]: 0, [P.ParamEyeBallY]: 0 } },
      { t: 0.4, p: { [P.ParamAngleZ]: -14, [P.ParamEyeBallY]: -0.8 } },
      { t: 1.2, p: { [P.ParamAngleZ]: -14, [P.ParamEyeBallY]: -0.8 } },
      { t: 1.6, p: { [P.ParamAngleZ]: 0, [P.ParamEyeBallY]: 0 } },
    ],
  },
  body_left: {
    dur: 1.4,
    keys: [
      { t: 0.0, p: { [P.ParamBodyAngleZ]: 0 } },
      { t: 0.35, p: { [P.ParamBodyAngleZ]: 10, [P.ParamAngleZ]: 6 } },
      { t: 1.0, p: { [P.ParamBodyAngleZ]: 10, [P.ParamAngleZ]: 6 } },
      { t: 1.4, p: { [P.ParamBodyAngleZ]: 0, [P.ParamAngleZ]: 0 } },
    ],
  },
  body_right: {
    dur: 1.4,
    keys: [
      { t: 0.0, p: { [P.ParamBodyAngleZ]: 0 } },
      { t: 0.35, p: { [P.ParamBodyAngleZ]: -10, [P.ParamAngleZ]: -6 } },
      { t: 1.0, p: { [P.ParamBodyAngleZ]: -10, [P.ParamAngleZ]: -6 } },
      { t: 1.4, p: { [P.ParamBodyAngleZ]: 0, [P.ParamAngleZ]: 0 } },
    ],
  },
  wave: {
    dur: 1.8,
    keys: [
      { t: 0.0, p: { [P.ParamAngleY]: 0 } },
      { t: 0.3, p: { [P.ParamAngleY]: 10 } },
      { t: 0.5, p: { [P.ParamAngleY]: -10 } },
      { t: 0.7, p: { [P.ParamAngleY]: 10 } },
      { t: 0.9, p: { [P.ParamAngleY]: -8 } },
      { t: 1.1, p: { [P.ParamAngleY]: 8 } },
      { t: 1.5, p: { [P.ParamAngleY]: 0 } },
    ],
  },
  point: {
    dur: 1.6,
    keys: [
      { t: 0.0, p: { [P.ParamAngleY]: 0, [P.ParamAngleZ]: 0 } },
      { t: 0.4, p: { [P.ParamAngleY]: -14, [P.ParamAngleZ]: 8 } },
      { t: 1.2, p: { [P.ParamAngleY]: -14, [P.ParamAngleZ]: 8 } },
      { t: 1.6, p: { [P.ParamAngleY]: 0, [P.ParamAngleZ]: 0 } },
    ],
  },
  shrink: {
    dur: 1.2,
    keys: [
      { t: 0.0, p: { 'Param157': 0 } },
      { t: 0.5, p: { 'Param157': 1.0 } },
      { t: 0.9, p: { 'Param157': 1.0 } },
      { t: 1.2, p: { 'Param157': 0 } },
    ],
    // 若模型沒有 Param157，退回整體縮放
    transform: { shrinkScale: 0.7, start: 0.3, end: 0.9 },
  },
};

// 指令間的最小間隔（秒），避免多個指令一口氣全部播放
const COMMAND_GAP = 0.35;

// ---------------------------------------------------------------------------
// 動畫輔助
// ---------------------------------------------------------------------------
function clamp(v, lo, hi) {
  return v < lo ? lo : v > hi ? hi : v;
}

function easeOut(t) {
  return 1 - (1 - t) * (1 - t);
}

function lerpKeyframes(keys, t) {
  if (t <= keys[0].t) return keys[0].p;
  const last = keys[keys.length - 1];
  if (t >= last.t) return last.p;
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i];
    const b = keys[i + 1];
    if (t >= a.t && t <= b.t) {
      const span = b.t - a.t || 1e-6;
      const k = easeOut((t - a.t) / span);
      const p = {};
      for (const name of new Set([...Object.keys(a.p), ...Object.keys(b.p)])) {
        const va = a.p[name] !== undefined ? a.p[name] : 0;
        const vb = b.p[name] !== undefined ? b.p[name] : 0;
        p[name] = va + (vb - va) * k;
      }
      return p;
    }
  }
  return last.p;
}

// ---------------------------------------------------------------------------
// 模型包裝：繼承 CubismUserModel，加入表情／動作／參數演出層
// ---------------------------------------------------------------------------
class ViewerModel extends CubismUserModel {
  constructor() {
    super();
    this.look = null;
    this.setting = null;
    this.layerOrder = null; // 使用中的模型切換時重新計算

    // 表情層：paramName → 目標值（持續套用）
    this.expression = {};
    this.exprSeq = 0;        // 表情指令序號：動作結束時用於判斷是否該自動還原
    this.exprSeqAtMotion = 0;
    this.autoNeutralPending = false;

    // 參數指令層：paramName → {from, to, start, dur}（過渡到目標後持續保持）
    this.held = {};

    // 動作層
    this.currentMotion = null;   // {keys, dur, t, scale, pointAmp}
    this.motionHold = 0;         // 動作結束後的保持時間

    // 口型
    this.lipsyncActive = false;
    this.lipsyncLevel = 0;
    this.lipsyncTick = 0;

    // 基礎效果開關（預設開啟；關閉後角色不會自動扭動／眨眼）
    this.autoBreath = true;
    this.autoBlink = true;
  }

  setParams(values) {
    if (!this._model) return;
    for (const name of Object.keys(values)) {
      this.setParam(name, values[name]);
    }
  }

  setParam(name, value) {
    if (!this._model) return;
    const id = pid(name);
    const index = this._model.getParameterIndex(id);
    if (index < 0) return;
    const min = this._model.getParameterMinimumValue(index);
    const max = this._model.getParameterMaximumValue(index);
    this._model.setParameterValueById(id, clamp(value, min, max));
  }

  /* 從參數名取得目前的參數值（無此參數時回傳 0） */
  getParam(name) {
    if (!this._model) return 0;
    const index = this._model.getParameterIndex(pid(name));
    if (index < 0) return 0;
    return this._model.getParameterValueById(pid(name));
  }

  /* 指令處理 */
  applyExpressionCmd(params) {
    const name = params && params.name;
    this.exprSeq += 1;
    // 先以 SWITCH_RESET 清空全部零件開關，再套用目標表情，避免開關殘留
    this.expression = Object.assign({}, SWITCH_RESET);
    if (name && EXPRESSIONS[name]) {
      Object.assign(this.expression, EXPRESSIONS[name]);
    }
    // 一次性歸零「未被新表情覆寫」的標準表情參數，避免前一表情殘留
    for (const key of Object.keys(FACIAL_RESET)) {
      if (!(key in this.expression)) {
        this.setParam(key, FACIAL_RESET[key]);
      }
    }
  }

  applyMotionCmd(params) {
    const name = params && params.name;
    const def = name && MOTIONS[name];
    if (!def) return;
    this.currentMotion = {
      keys: def.keys,
      dur: def.dur,
      t: 0,
      scaleFrom: 1.0,
      scaleTo: def.transform ? def.transform.shrinkScale : 1.0,
      transform: def.transform || null,
    };
    // 記錄動作開始時的表情序號，供動作結束後判斷是否要自動還原表情
    this.exprSeqAtMotion = this.exprSeq;
    // 動作重新開始，不需要舊層殘留
    this.motionHold = 0;
  }

  applyParameterCmd(params) {
    const idName = params && params.id;
    if (!idName || typeof idName !== 'string') return;
    let value = Number(params.value);
    if (!isFinite(value)) value = 0;
    const dur = Number(params.duration) > 0 ? Number(params.duration) : 0.8;
    this.held[idName] = { from: this.getParam(idName), to: value, start: 0, dur };
  }

  stopCmds() {
    this.currentMotion = null;
    this.motionHold = 0;
  }

  resetAll() {
    this.stopCmds();
    this.expression = Object.assign({}, SWITCH_RESET);
    this.setParams(FACIAL_RESET);
    this.held = {};
    this.lipsyncLevel = 0;
    this.lipsyncActive = false;
  }

  /* 每幀更新（在 model.loadParameters() 之後呼叫） */
  updateFrame(delta, time) {
    const model = this._model;
    if (!model) return;

    // 動作結束後自動還原中性表情：延後到幀開頭執行，
    // 避免同幀 foldExpression 已用舊表情建好 pending 並在最後覆蓋還原值。
    if (this.autoNeutralPending) {
      this.autoNeutralPending = false;
      if (this.exprSeq === this.exprSeqAtMotion && Object.keys(this.expression).length > 0) {
        this.applyExpressionCmd({ name: 'neutral' });
      }
    }

    // 基礎效果：眨眼、呼吸、視線跟隨（可個別關閉避免角色自動扭動）
    if (this._eyeBlink && this.autoBlink) {
      this._eyeBlink.updateParameters(model, delta);
    }
    if (this._breath && this.autoBreath) {
      this._breath.updateParameters(model, delta);
    }
    if (this.look && this._dragManager) {
      this.look.updateParameters(model, this._dragManager.getX(), this._dragManager.getY());
    }

    // 疊加層：表情 → 參數指令 → 動作 → 口型
    const pending = {};
    this.foldExpression(pending);
    this.foldHeld(pending, delta);
    this.foldMotion(pending, delta);
    this.foldLipsync(pending, delta);

    // 先交給物理演算，再用指令層覆蓋，確保表情／動作／口型不被物理還原
    if (this._physics) {
      this._physics.evaluate(model, delta);
    }

    this._applyPending(pending);

    // 姿勢（Pose）：更新零件透明度，與參數演算分離
    if (this._pose) {
      this._pose.updateParameters(model, delta);
    }
    model.update();

    // 整體縮放（特殊動作：無 Param157 時的轉換替代）
    return this.effectiveScale;
  }

  foldExpression(pending) {
    for (const name of Object.keys(this.expression)) {
      pending[name] = this.expression[name];
    }
  }

  foldHeld(pending, delta) {
    for (const name of Object.keys(this.held)) {
      const h = this.held[name];
      h.start += delta;
      const t = clamp(h.start / h.dur, 0, 1);
      const v = h.from + (h.to - h.from) * easeOut(t);
      pending[name] = v;
    }
  }

  foldMotion(pending, delta) {
    if (!this.currentMotion) {
      return;
    }
    const m = this.currentMotion;
    m.t += delta;
    const t = m.t;
    if (m.transform) {
      // 縮放轉換（時間軸自成一格）
      const s0 = m.transform.start;
      const s1 = m.transform.end;
      if (t <= s0) {
        m.effectiveScale = m.scaleFrom;
      } else if (t >= s1) {
        m.effectiveScale = m.scaleTo;
      } else {
        const k = easeOut((t - s0) / (s1 - s0));
        m.effectiveScale = m.scaleFrom + (m.scaleTo - m.scaleFrom) * k;
      }
    }
    const p = lerpKeyframes(m.keys, t);
    for (const name of Object.keys(p)) {
      pending[name] = p[name];
    }
    if (t >= m.dur) {
      this.currentMotion = null;
      // 動作結束後自動還原為中性表情，避免角色一直維持最後一個表情。
      // 只在動作進行期間表情沒有被改動過時才還原，不覆蓋新到達的表情指令。
      if (this.exprSeq === this.exprSeqAtMotion && Object.keys(this.expression).length > 0) {
        this.autoNeutralPending = true;
      }
    }
  }

  foldLipsync(pending, delta) {
    if (!this.lipsyncActive) {
      this.lipsyncLevel = Math.max(0, this.lipsyncLevel - delta * 6);
      if (this.lipsyncLevel <= 0) {
        return;
      }
    }
    this.lipsyncTick += delta;
    const base = this.expression['ParamMouthOpenY'] || 0;
    const wave =
      0.5 +
      0.5 * Math.max(Math.sin(this.lipsyncTick * 9), Math.sin(this.lipsyncTick * 17 + 1.7));
    const amp = 0.18 * this.lipsyncLevel;
    pending['ParamMouthOpenY'] = Math.max(base, clamp(base + amp * wave, 0, 1));
  }

  _applyPending(pending) {
    for (const name of Object.keys(pending)) {
      this.setParam(name, pending[name]);
    }
  }

  get effectiveScale() {
    return this.currentMotion && this.currentMotion.effectiveScale
      ? this.currentMotion.effectiveScale
      : 1.0;
  }
}

// ---------------------------------------------------------------------------
// 應用程式主體
// ---------------------------------------------------------------------------
class ViewerApp {
  constructor(canvas) {
    this.canvas = canvas;
    this.gl = null;
    this.fbo = null;
    this.userModel = null;
    this.dragX = 0;
    this.dragY = 0;
    this.commandQueue = [];
    this.lastCommandAt = 0;
    this.lastTime = performance.now() / 1000;
    this.modelRel = null;
    this.loadingRel = null;
    this.overlay = document.getElementById('overlay');
    this.statusEl = document.getElementById('conn-status');
    this.modelSelect = document.getElementById('model-select');
    this.tabHidden = document.hidden;
    this.autoBreath = true; // 呼吸晃動開關（切換模型時沿用）
    this.autoBlink = true;  // 自動眨眼開關

    this._initGl();
    this._bindEvents();
    this._initPoll();
    this._loop();
  }

  _initGl() {
    const options = {
      alpha: true,
      depth: true,
      premultipliedAlpha: true,
      antialias: true,
      preserveDrawingBuffer: false,
    };
    this.gl =
      this.canvas.getContext('webgl', options) ||
      this.canvas.getContext('experimental-webgl', options);
    if (!this.gl) {
      this._showOverlay(
        '無法建立 WebGL 環境。\n請改用支援 WebGL 的瀏覽器（Chrome／Edge／Firefox）。'
      );
      return;
    }
    this.fbo = this.gl.getParameter(this.gl.FRAMEBUFFER_BINDING);
  }

  _bindEvents() {
    window.addEventListener('resize', () => this._resize());
    this.canvas.addEventListener('pointerdown', (e) => this._pointerDown(e));
    this.canvas.addEventListener('pointermove', (e) => this._pointerMove(e));
    this.canvas.addEventListener('pointerup', () => this._pointerUp());
    this.canvas.addEventListener('pointercancel', () => this._pointerUp());
    this.modelSelect.addEventListener('change', () => this._onModelChange());
    document.addEventListener('visibilitychange', () => {
      this.tabHidden = document.hidden;
    });
  }

  _resize() {
    const w = this.canvas.clientWidth || window.innerWidth;
    const h = this.canvas.clientHeight || window.innerHeight;
    this.canvas.width = w;
    this.canvas.height = h;
    if (this.userModel && this.userModel.getRenderer()) {
      this.userModel.getRenderer().setRenderTargetSize(w, h);
    }
  }

  _pointerDown(e) {
    this.canvas.setPointerCapture(e.pointerId);
    this._updateDrag(e);
  }

  _pointerMove(e) {
    this._updateDrag(e);
  }

  _pointerUp() {
    // 放開後視線緩慢回到正中
  }

  _updateDrag(e) {
    const rect = this.canvas.getBoundingClientRect();
    // 畫面座標 → 邏輯座標（-1..1，左上為原點）
    this.dragX = clamp(((e.clientX - rect.left) / rect.width) * 2 - 1, -1, 1);
    this.dragY = clamp(((e.clientY - rect.top) / rect.height) * 2 - 1, -1, 1);
    if (this.userModel) {
      this.userModel.setDragging(this.dragX, this.dragY);
    }
  }

  _showOverlay(text) {
    this.overlay.textContent = text;
    this.overlay.classList.remove('hidden');
  }

  _hideOverlay() {
    this.overlay.classList.add('hidden');
  }

  /* ---------- 模型載入 ---------- */

  _onModelChange() {
    const rel = this.modelSelect.value;
    if (rel && rel !== this.modelRel && rel !== this.loadingRel) {
      this.loadModel(rel);
    }
  }

  async fillModels() {
    try {
      const res = await fetch('/api/models');
      const data = await res.json();
      const list = data.models || [];
      this.modelSelect.innerHTML = '';
      for (const m of list) {
        const opt = document.createElement('option');
        opt.value = m.rel;
        opt.textContent = m.name;
        this.modelSelect.appendChild(opt);
      }
      if (list.length) {
        this.modelSelect.disabled = false;
        this.modelSelect.value = list[0].rel;
      } else {
        this._showOverlay('3D 目錄下找不到任何 .model3.json 模型。');
      }
      if (list.length) {
        this.loadModel(list[0].rel);
      }
    } catch (err) {
      this._showOverlay(`無法取得模型清單：\n${err}`);
    }
  }

  async loadModel(rel) {
    if (this.loadingRel) return;
    this.loadingRel = rel;
    this._hideOverlay();
    try {
      if (this.userModel) {
        this.userModel.release();
        this.userModel = null;
      }
      this.modelRel = null;
      this.commandQueue = [];

      const setting = new CubismModelSettingJson(
        await this._fetchArrayBuffer(`/model/${encRel(rel)}`)
      );
      const dir = rel.slice(0, rel.lastIndexOf('/') + 1);

      const userModel = new ViewerModel();
      userModel.autoBreath = this.autoBreath;
      userModel.autoBlink = this.autoBlink;
      const mocBuffer = await this._fetchArrayBuffer(
        `/model/${encRel(dir)}${encFile(setting.getModelFileName())}`
      );
      userModel.loadModel(mocBuffer);

      // 物理演算
      const physicsFile = setting.getPhysicsFileName();
      if (physicsFile) {
        const physBuffer = await this._fetchArrayBuffer(
          `/model/${encRel(dir)}${encFile(physicsFile)}`
        );
        userModel.loadPhysics(physBuffer, physBuffer.byteLength);
      }

      // 姿勢（Pose）：切換手臂等零件組，避免模型同時顯示多組零件
      const poseFile = setting.getPoseFileName();
      if (poseFile) {
        const poseBuffer = await this._fetchArrayBuffer(
          `/model/${encRel(dir)}${encFile(poseFile)}`
        );
        userModel.loadPose(poseBuffer, poseBuffer.byteLength);
      }

      // 呼吸
      const breath = CubismBreath.create();
      const pi = pid;
      breath.setParameters([
        new BreathParameterData(pi(P.ParamAngleX), 0, 15, 6.5345, 0.5),
        new BreathParameterData(pi(P.ParamAngleY), 0, 8, 3.5345, 0.5),
        new BreathParameterData(pi(P.ParamAngleZ), 0, 10, 5.5345, 0.5),
        new BreathParameterData(pi(P.ParamBodyAngleX), 0, 4, 15.5345, 0.5),
        new BreathParameterData(pi(P.ParamBreath), 0.5, 0.5, 3.2345, 1),
      ]);
      userModel._breath = breath;

      // 視線跟隨
      const look = CubismLook.create();
      look.setParameters([
        new LookParameterData(pi(P.ParamAngleX), 30, 0, 0),
        new LookParameterData(pi(P.ParamAngleY), 0, 30, 0),
        new LookParameterData(pi(P.ParamAngleZ), 0, 0, -30),
        new LookParameterData(pi(P.ParamBodyAngleX), 10, 0, 0),
        new LookParameterData(pi(P.ParamEyeBallX), 1, 0, 0),
        new LookParameterData(pi(P.ParamEyeBallY), 0, 1, 0),
      ]);
      userModel.look = look;

      // 眨眼（依 model3.json 的 EyeBlink group）
      if (setting.getEyeBlinkParameterCount() > 0) {
        userModel._eyeBlink = CubismEyeBlink.create(setting);
      }

      // 版面（如有 Layout 設定）
      const layout = new Map();
      setting.getLayoutMap(layout);
      if (userModel.getModelMatrix()) {
        userModel.getModelMatrix().setupFromLayout(layout);
      }

      userModel.createRenderer(this.canvas.width, this.canvas.height);
      userModel.getRenderer().startUp(this.gl);
      userModel.getRenderer().loadShaders();
      userModel.getRenderer().setRenderTargetSize(this.canvas.width, this.canvas.height);
      userModel.getRenderer().setIsPremultipliedAlpha(true);

      await this._setupTextures(userModel, setting, dir);
      userModel.setting = setting;

      this.userModel = userModel;
      this.modelRel = rel;
      this._hideOverlay();
    } catch (err) {
      this._showOverlay(`模型載入失敗：\n${err || '未知錯誤'}\n\n請按下拉選單選擇其他模型。`);
    } finally {
      this.loadingRel = null;
    }
  }

  async _fetchArrayBuffer(url) {
    const res = await fetch(url);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status} ${url}`);
    }
    return res.arrayBuffer();
  }

  async _setupTextures(userModel, setting, dir) {
    const renderer = userModel.getRenderer();
    const count = setting.getTextureCount();
    const jobs = [];
    for (let i = 0; i < count; i++) {
      const texName = setting.getTextureFileName(i);
      if (!texName) continue;
      const index = i;
      jobs.push(
        loadImage(`/model/${encRel(dir)}${encFile(texName)}`)
          .then((img) => {
            const tex = this._createTexture(img);
            renderer.bindTexture(index, tex);
            return tex;
          })
          .catch((err) => {
            console.warn('紋理載入失敗：', texName, err);
          })
      );
    }
    await Promise.all(jobs);
  }

  _createTexture(img) {
    const gl = this.gl;
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.bindTexture(gl.TEXTURE_2D, null);
    return tex;
  }

  /* ---------- 指令輪詢 ---------- */

  _initPoll() {
    const tick = async () => {
      try {
        const res = await fetch(`/api/poll?cb=${Date.now()}`);
        if (res.ok) {
          const data = await res.json();
          const cmds = data.commands || [];
          for (const c of cmds) {
            this.commandQueue.push(c);
          }
          this.statusEl.textContent = '已連線';
          this.statusEl.className = 'on';
        }
      } catch (err) {
        this.statusEl.textContent = '未連線';
        this.statusEl.className = 'off';
      }
      setTimeout(tick, 300);
    };
    tick();
  }

  _drainCommands(now) {
    if (!this.userModel) return;
    if (this.commandQueue.length === 0) return;
    if (now - this.lastCommandAt < COMMAND_GAP) return;
    const cmd = this.commandQueue.shift();
    this.lastCommandAt = now;
    try {
      const a = cmd.action;
      if (a === 'expression') {
        this.userModel.applyExpressionCmd(cmd.params);
      } else if (a === 'motion') {
        this.userModel.applyMotionCmd(cmd.params);
      } else if (a === 'parameter') {
        this.userModel.applyParameterCmd(cmd.params);
      } else if (a === 'lipsync') {
        const active = !!(cmd.params && cmd.params.active);
        this.userModel.lipsyncActive = active;
        if (active) {
          this.userModel.lipsyncLevel = 1;
        }
      } else if (a === 'stop') {
        this.userModel.stopCmds();
      } else if (a === 'reset') {
        this.userModel.resetAll();
      }
    } catch (err) {
      console.warn('指令處理失敗：', err);
    }
  }

  /* ---------- 主迴圈 ---------- */

  _loop() {
    requestAnimationFrame(() => this._loop());
    const now = performance.now() / 1000;
    let delta = now - this.lastTime;
    this.lastTime = now;
    if (delta > 0.1) delta = 0.1; // 切分頁回來時避免暴衝
    if (this.tabHidden) return;

    if (!this.gl || !this.userModel || !this.userModel.getModel()) {
      return;
    }
    this._drainCommands(now);

    const gl = this.gl;
    const w = this.canvas.width;
    const h = this.canvas.height;

    gl.viewport(0, 0, w, h);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    /* 投影：仿照 SDK Demo（LAppLive2DManager.onUpdate）
       模型座標原點在畫布中心；先用 modelMatrix 把畫布縮放到螢幕，
       再依視窗長寬比補償，最後加 0.9 外框與動作縮放。 */
    const model = this.userModel.getModel();
    const mw = model.getCanvasWidth() || 1;
    const proj = new CubismMatrix44();
    if (mw > 1.0 && w < h) {
      this.userModel.getModelMatrix().setWidth(2.0);
      proj.scale(1.0, w / h);
    } else {
      proj.scale(h / w, 1.0);
    }
    const eff = this.userModel.effectiveScale || 1;
    proj.scaleRelative(0.9 * eff, 0.9 * eff);

    this.userModel.updateFrame(delta, now);

    const mvp = new CubismMatrix44();
    mvp.multiplyByMatrix(proj);
    if (this.userModel.getModelMatrix()) {
      mvp.multiplyByMatrix(this.userModel.getModelMatrix());
    }
    const renderer = this.userModel.getRenderer();
    renderer.setMvpMatrix(mvp);
    renderer.setRenderState(this.fbo, [0, 0, w, h]);
    renderer.drawModel();
  }
}

/* ---------- 工具函式 ---------- */

function encRel(rel) {
  return rel ? rel.split('/').map(encodeURIComponent).join('/') : '';
}

function encFile(name) {
  return name.split('/').map(encodeURIComponent).join('/');
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = (err) => reject(err);
    img.src = url;
  });
}

/* ---------- 動作測試面板 ---------- */

const EXPR_LABELS = {
  neutral: '平常',
  happy: '開心',
  angry: '生氣',
  sad: '難過',
  surprised: '驚訝',
  love: '愛慕',
  cry: '哭泣',
  blush: '害羞',
  sleep: '想睡',
  worried: '擔心',
  smug: '得意',
};

const MOTION_LABELS = {
  nod: '點頭',
  shake: '搖頭',
  look_left: '看左',
  look_right: '看右',
  look_up: '看上',
  look_down: '看下',
  body_left: '左傾',
  body_right: '右傾',
  wave: '揮手',
  point: '指向',
  shrink: '縮小',
};

const PARAM_SUGGEST = [
  'ParamAngleX',
  'ParamAngleY',
  'ParamAngleZ',
  'ParamBodyAngleX',
  'ParamBodyAngleY',
  'ParamBodyAngleZ',
  'ParamEyeBallX',
  'ParamEyeBallY',
  'ParamMouthOpenY',
  'ParamMouthForm',
  'ParamEyeLOpen',
  'ParamEyeROpen',
  'ParamBrowLY',
  'ParamBrowRY',
];

let tpMsgTimer = null;
let tpPosting = false;

function tpMsg(text, isErr) {
  const el = document.getElementById('tp-msg');
  if (!el) return;
  el.textContent = text;
  el.className = 'tp-msg' + (isErr ? ' err' : '');
  clearTimeout(tpMsgTimer);
  tpMsgTimer = setTimeout(() => {
    el.textContent = '';
  }, 1800);
}

async function sendCommand(action, params) {
  if (tpPosting) return;
  tpPosting = true;
  try {
    const res = await fetch('/api/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, params: params || {} }),
    });
    const data = await res.json();
    if (res.ok && data.status === 'ok') {
      tpMsg(`送出：${action}（佇列 ${data.queued}）`);
      return true;
    }
    tpMsg(`錯誤：${data.error || res.status}`, true);
    return false;
  } catch (err) {
    tpMsg(`送出失敗：${err}`, true);
    return false;
  } finally {
    tpPosting = false;
  }
}

function tpButton(text, onClick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = text;
  b.addEventListener('click', onClick);
  return b;
}

/* 開關型按鈕：依目前狀態切換 .toggled 樣式與功能 */
function tpToggle(text, getVal, onToggle) {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = text;
  b.classList.toggle('toggled', !!getVal());
  b.addEventListener('click', () => {
    onToggle(!getVal());
    b.classList.toggle('toggled', !!getVal());
  });
  return b;
}

function tpGroup(title) {
  const box = document.createElement('div');
  box.className = 'tp-group';
  const h = document.createElement('div');
  h.className = 'tp-title';
  h.textContent = title;
  const btns = document.createElement('div');
  btns.className = 'tp-btns';
  box.appendChild(h);
  box.appendChild(btns);
  return { box, btns };
}

function initTestPanel(app) {
  const body = document.getElementById('testpanel-body');
  const toggle = document.getElementById('testpanel-toggle');
  if (!body || !toggle) return;
  toggle.addEventListener('click', () => body.classList.toggle('hidden'));

  const exp = tpGroup('表情');
  for (const name of Object.keys(EXPRESSIONS)) {
    exp.btns.appendChild(tpButton(EXPR_LABELS[name] || name, () => sendCommand('expression', { name })));
  }
  body.appendChild(exp.box);

  const mot = tpGroup('動作');
  for (const name of Object.keys(MOTIONS)) {
    mot.btns.appendChild(tpButton(MOTION_LABELS[name] || name, () => sendCommand('motion', { name })));
  }
  body.appendChild(mot.box);

  const ctl = tpGroup('控制');
  ctl.btns.appendChild(
    tpToggle('呼吸晃動', () => app.autoBreath, (v) => {
      app.autoBreath = v;
      if (app.userModel) app.userModel.autoBreath = v;
    })
  );
  ctl.btns.appendChild(
    tpToggle('自動眨眼', () => app.autoBlink, (v) => {
      app.autoBlink = v;
      if (app.userModel) app.userModel.autoBlink = v;
    })
  );
  ctl.btns.appendChild(
    tpToggle('口型同步', () => (app.userModel ? !!app.userModel.lipsyncActive : false), (v) => {
      if (app.userModel) app.userModel.lipsyncActive = v;
    })
  );
  ctl.btns.appendChild(tpButton('停止動作', () => sendCommand('stop')));
  ctl.btns.appendChild(tpButton('全部還原', () => sendCommand('reset')));
  body.appendChild(ctl.box);

  const pgrp = tpGroup('參數測試');
  const datalist = document.createElement('datalist');
  datalist.id = 'tp-param-suggest';
  for (const s of PARAM_SUGGEST) {
    const o = document.createElement('option');
    o.value = s;
    datalist.appendChild(o);
  }
  const idInput = document.createElement('input');
  idInput.type = 'text';
  idInput.id = 'tp-param-id';
  idInput.placeholder = '參數 ID';
  idInput.value = 'ParamAngleZ';
  idInput.setAttribute('list', 'tp-param-suggest');
  const valInput = document.createElement('input');
  valInput.type = 'number';
  valInput.id = 'tp-param-val';
  valInput.value = '15';
  valInput.step = 'any';
  pgrp.btns.appendChild(idInput);
  pgrp.btns.appendChild(datalist);
  pgrp.btns.appendChild(valInput);
  pgrp.btns.appendChild(
    tpButton('套用參數', () =>
      sendCommand('parameter', { id: idInput.value.trim(), value: Number(valInput.value) })
    )
  );
  body.appendChild(pgrp.box);
}

/* ---------- 啟動 ---------- */

const canvas = document.getElementById('canvas');
CubismFramework.startUp();
CubismFramework.initialize();

const app = new ViewerApp(canvas);
app._resize();
initTestPanel(app);
window.__app = app; // 供頁面測試／外部腳本存取
app.fillModels();