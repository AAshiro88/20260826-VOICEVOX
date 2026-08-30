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

// ---------------------------------------------------------------------------
// 層一：模型自帶 motion3 播放
// ---------------------------------------------------------------------------
// 本專案使用的第三方模型，其 .motion3.json 並非官方 SDK 的 [type,t,v] 段編碼
// （分段標記與關鍵幀依序混排），官方 CubismMotion 無法直接解析。
// 因此以寬鬆解碼抽出遞增時間的 (t, v) 關鍵幀序列，播放時以線性插值求值。
function softDecodeSegments(seg) {
  const pts = [{ t: seg[0], v: seg[1] }];
  let i = 2;
  const len = seg.length;
  while (i < len) {
    const x = seg[i];
    // 0/1 是段界標記：若其後接著可維持時間單調的關鍵幀，即視作標記而略過
    if ((x === 0 || x === 1) && i + 2 < len) {
      const a = seg[i + 1];
      if (!(a === 0 || a === 1) && a >= pts[pts.length - 1].t - 1e-9) {
        i += 1;
        continue;
      }
    }
    if (i + 1 >= len) break;
    pts.push({ t: seg[i], v: seg[i + 1] });
    i += 2;
  }
  return pts;
}

function evalSegments(pts, t, loop, dur) {
  if (loop && dur > 0) t = t % dur;
  const last = pts[pts.length - 1];
  if (t <= pts[0].t) return pts[0].v;
  if (t >= last.t) return last.v;
  for (let i = 0; i < pts.length - 1; i++) {
    if (t >= pts[i].t && t <= pts[i + 1].t) {
      const span = pts[i + 1].t - pts[i].t || 1e-6;
      return pts[i].v + (pts[i + 1].v - pts[i].v) * ((t - pts[i].t) / span);
    }
  }
  return last.v;
}

// 指令 → 模型自帶動作的預設對應（依曲線 signature 推測，可於執行期微調）。
// 值為 model3.json Motions 中 .motion3.json 的檔案主檔名；未列出者沿用合成動作。
const MOTION_MAP = {
  Hiyori: {
    wave: 'Hiyori_m08',       // 2.1s，ArmB/HandB 大幅擺動＝揮手
    point: 'Hiyori_m04',      // TapBody 群組＝點擊／指向
    body_left: 'Hiyori_m06',  // 5.4s，雙臂 B 手部大幅動作
    body_right: 'Hiyori_m10', // 4.2s，身體左右微傾配合手臂
  },
  Mao: {
    wave: 'special_01', // 7.8s，右手臂 B＋右手大幅＝揮手
    point: 'mtn_04',    // 4.2s，左臂 B 大幅
    body_left: 'mtn_03',
    body_right: 'special_02',
  },
};

// ---------------------------------------------------------------------------
// 層二：參數適配（模型綁定參數若與標準參數不同，改寫到真實綁定參數）
// ---------------------------------------------------------------------------
// 神宫白子（面饼）沒有 ParamBodyAngleX/Y/Z 與標準手部參數，
// 身體與手部改用自訂參數：Param49/50/51＝身體 x/z/y，Param58/59/60＝左手 1/2/3，
// Param69/70＝右手 1/2。寫入時將標準語意參數乘以增益後鏡射到綁定參數，
// 避免指令寫入不存在的標準參數而完全沒有動作。
const CHANNEL_ADAPTERS = {
  '神宫白子': {
    ParamBodyAngleX: [['Param49', 1.0]],
    ParamBodyAngleY: [['Param50', 1.0]],
    ParamBodyAngleZ: [['Param49', 1.0]],
    ParamHandL: [['Param58', 1], ['Param59', 1], ['Param60', 1]],
    ParamHandR: [['Param69', 1], ['Param70', 1]],
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

// 點擊位置（邏輯座標 -1..1，上為負）→ 部位動作；'chestTouch' 表示觸發臨時表情
function tapZone(dx, dy) {
  if (Math.abs(dx) >= 0.3) {
    // 左右臂區：右側偏上為揮手，其餘左／右傾
    return dx >= 0.3 ? (dy < -0.1 ? 'wave' : 'body_right') : 'body_left';
  }
  if (dy < -0.3) return 'nod';         // 頭部／臉
  if (dy < -0.05) return 'chestTouch'; // 胸部（害羞表情）
  if (dy < 0.55) return 'point';       // 腰部
  return 'shake';                      // 下緣／腿部
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
    this.lookFollow = true; // 視線跟隨游標開關（由 ViewerApp 於切換模型時沿用）
    this.tempExpr = null;   // 臨時表情（點擊胸部害羞）：{seq,dur,t,keys}，時間到自動還原
    this.tempExprSeq = 0;   // 臨時表情序號（再次觸發即重置計時）
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

    // 層一：模型自帶動作
    this.modelKey = null;          // 模型識別鍵（rel 第一段），用於 MOTION_MAP／適配表
    this.nativeMotions = [];       // [{id, group, file, dur, loop, fadeIn, fadeOut, curves}]
    this.nativeMotionsById = {};   // 檔案主檔名 → nativeMotions 索引
    this.nativeMotion = null;      // 目前播放中 {data, t}
    this.partOpacity = {};         // 原生動作寫入的零件透明度 name→值
    this.partOpacityBase = {};     // 受影響零件的原透明度，結束時還原

    // 口型
    this.lipsyncActive = false;
    this.lipsyncLevel = 0;
    this.lipsyncTick = 0;

    // 自然搖擺：低幅、長週期、似隨機的站姿微晃（與呼吸獨立）
    this.idleSwayOn = true;
    this.idleSwayAmp = 1;
    this._swayT = 0;
    // 呼吸：累積時間與依照滑桿的幅度／頻率倍率
    this._breathT = 0;
    this._breathAmp = 1;
    this._breathFreq = 1;

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
    // 層二：若此模型把標準語意參數鏡射到自訂綁定參數，改寫後再輸出
    const adapter = this.modelKey ? CHANNEL_ADAPTERS[this.modelKey] : null;
    const maps = adapter && adapter[name];
    if (maps) {
      for (const [target, gain] of maps) {
        this._writeParam(target, value * gain);
      }
      return;
    }
    this._writeParam(name, value);
  }

  _writeParam(name, value) {
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

  /* 點擊胸部：觸發短暫害羞表情，時間到自動還原（不碰既有表情層） */
  triggerChestBlush(durSec = 1.2) {
    this.tempExprSeq += 1;
    const keys = Object.assign({}, EXPRESSIONS.blush || {});
    // 快照觸發前實際存在的參數值，結束時還原；不存在於此模型的參數不寫入也不還原
    const snap = {};
    if (this._model) {
      for (const name of Object.keys(keys)) {
        const idx = this._model.getParameterIndex(pid(name));
        if (idx >= 0) {
          snap[name] = this._model.getParameterValueByIndex(idx);
        }
      }
    }
    this.tempExpr = { seq: this.tempExprSeq, dur: durSec, t: 0, keys, snap };
  }

  applyMotionCmd(params) {
    const name = params && params.name;
    // 層一：此模型若有對應的自帶動作，優先播放原生 motion3
    if (name && this.startNativeMotion(name)) {
      return;
    }
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

  /* 依指令名稱啟動模型自帶動作；無對應動作時回傳 false */
  startNativeMotion(name) {
    const map =
      this.modelKey && MOTION_MAP[this.modelKey] ? MOTION_MAP[this.modelKey] : null;
    const key = map ? map[name] : null;
    const idx = key != null ? this.nativeMotionsById[key] : -1;
    if (idx < 0) return false;
    const data = this.nativeMotions[idx];
    // 先記錄零件原本透明度，動作結束時還原
    for (const c of data.curves) {
      if (c.target === 'PartOpacity') {
        this.partOpacityBase[c.id] = this._model.getPartOpacityById(pid(c.id));
      }
    }
    this.nativeMotion = { data, t: 0 };
    this.currentMotion = null;
    this.motionHold = 0;
    this.exprSeqAtMotion = this.exprSeq;
    return true;
  }

  _stopNativeMotion() {
    if (this.partOpacity && this._model) {
      for (const name of Object.keys(this.partOpacity)) {
        const base = name in this.partOpacityBase ? this.partOpacityBase[name] : 1;
        this._model.setPartOpacityById(pid(name), base);
      }
    }
    this.nativeMotion = null;
    this.partOpacity = {};
    this.partOpacityBase = {};
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
    this._stopNativeMotion();
  }

  resetAll() {
    this.stopCmds();
    this.expression = Object.assign({}, SWITCH_RESET);
    this.setParams(FACIAL_RESET);
    this.held = {};
    this.lipsyncLevel = 0;
    this.lipsyncActive = false;
  }

  /* 每幀更新（先還原為載入時的初始參數，再做效果疊加，避免值漂移） */
  updateFrame(delta, time) {
    const model = this._model;
    if (!model) return;

    // 將參數還原為載入時的初值，作為本幀基準；
    // 未還原時效果的疊加會累積到前一幀的值上，導致參數往範圍邊界漂移、畫面大幅跳變。
    model.loadParameters();

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
    if (this._breathAmp != null && this.autoBreath) {
      this._updateBreath(delta);
    }
    // 自然搖擺：與呼吸／眨眼獨立，可個別開啟或調整幅度
    if (this.idleSwayOn) {
      this._updateIdleSway(delta);
    }
    if (this.look && this._dragManager && this.lookFollow) {
      // 視線跟隨：每幀推進視線逼近游標目標（缺這步視線會永遠停在 0）
      this._dragManager.update(delta);
      this.look.updateParameters(model, this._dragManager.getX(), this._dragManager.getY());
    }

    // 疊加層：表情 → 參數指令 → 動作 → 口型
    const pending = {};
    this.foldExpression(pending);
    this.foldHeld(pending, delta);
    this.foldMotion(pending, delta);
    this.foldLipsync(pending, delta);
    // 臨時表情（點擊胸部害羞）：疊在最上層，時間到還原觸發前的參數值
    if (this.tempExpr) {
      this.tempExpr.t += delta;
      if (this.tempExpr.t >= this.tempExpr.dur || this.tempExpr.seq !== this.tempExprSeq) {
        if (this.tempExpr.snap) {
          for (const name of Object.keys(this.tempExpr.snap)) {
            this.setParam(name, this.tempExpr.snap[name]);
          }
        }
        this.tempExpr = null;
      } else {
        for (const name of Object.keys(this.tempExpr.keys)) {
          pending[name] = this.tempExpr.keys[name];
        }
      }
    }

    // 先交給物理演算，再用指令層覆蓋，確保表情／動作／口型不被物理還原
    if (this._physics) {
      this._physics.evaluate(model, delta);
    }

    this._applyPending(pending);

    // 姿勢（Pose）：更新零件透明度，與參數演算分離
    if (this._pose) {
      this._pose.updateParameters(model, delta);
    }
    this._applyPendingPartOpacity();
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
    // 層一：原生動作優先於合成動作
    if (this.nativeMotion) {
      this._foldNativeMotion(pending, delta);
      return;
    }
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

  /* 原生 motion3 求值：線性插值、淡入淡出、結束時還原零件透明度 */
  _foldNativeMotion(pending, delta) {
    const m = this.nativeMotion;
    const d = m.data;
    m.t += delta;
    const t = m.t;
    const dur = d.dur;

    // 淡入／淡出增益（於 fade 秒內線性收斂）
    let gain = 1;
    if (t < d.fadeIn) gain = Math.max(0.0001, t / d.fadeIn);
    const fadeOutStart = dur - d.fadeOut;
    if (t > fadeOutStart) gain = Math.max(0, (dur - t) / d.fadeOut);

    for (const c of d.curves) {
      const v = evalSegments(c.pts, Math.min(t, dur), false, dur);
      if (c.target === 'PartOpacity') {
        this.partOpacity[c.id] = clamp(v * gain, 0, 1);
      } else {
        pending[c.id] = v * gain;
      }
    }

    if (t >= dur) {
      this._stopNativeMotion();
      // 動作結束後自動還原中性表情（同合成動作）
      if (this.exprSeq === this.exprSeqAtMotion && Object.keys(this.expression).length > 0) {
        this.autoNeutralPending = true;
      }
    }
  }

  /* 在 pose 之後套用原生動作的零件透明度，確保零件切換不會被 pose 覆寫 */
  _applyPendingPartOpacity() {
    for (const name of Object.keys(this.partOpacity)) {
      this._model.setPartOpacityById(pid(name), this.partOpacity[name]);
    }
  }

  /* 以 clamp 方式將增量疊加到參數：目標值先限制在參數範圍內，
   避免 repeat 參數跳出範圍而被 SDK wrap 到對向極限 */
  _clampedAdd(id, value) {
    const model = this._model;
    const index = model.getParameterIndex(pid(id));
    if (index < 0) return;
    const min = model.getParameterMinimumValue(index);
    const max = model.getParameterMaximumValue(index);
    const cur = model.getParameterValueByIndex(index);
    model.setParameterValueByIndex(index, clamp(cur + value, min, max));
  }

  /* 呼吸：極微幅、長週期的多軸正弦，對 ParamBreath 做正常起伏 */
  _updateBreath(delta) {
    this._breathT += delta;
    const t = this._breathT;
    const twoPi = 2 * Math.PI;
    const amp = this._breathAmp;
    const freq = this._breathFreq;
    const breath = [
      { id: P.ParamAngleX, peak: 0.5, cycle: 8.0, phase: 0.2 },
      { id: P.ParamAngleY, peak: 0.4, cycle: 8.5, phase: 1.1 },
      { id: P.ParamAngleZ, peak: 0.6, cycle: 7.5, phase: 2.0 },
      { id: P.ParamBodyAngleX, peak: 0.3, cycle: 9.0, phase: 3.4 },
    ];
    for (const b of breath) {
      const v = b.peak * amp * Math.sin((twoPi * t) / (b.cycle / freq) + b.phase);
      this._clampedAdd(b.id, v);
    }
    // ParamBreath：0.5 上下正常呼吸起伏
    const mindex = this._model.getParameterIndex(pid(P.ParamBreath));
    if (mindex >= 0) {
      const min = this._model.getParameterMinimumValue(mindex);
      const max = this._model.getParameterMaximumValue(mindex);
      const v = 0.5 + 0.5 * Math.sin((twoPi * t) / 3.2345);
      this._model.setParameterValueByIndex(mindex, clamp(v, min, max));
    }
  }

  /* 自然搖擺：以多個不可通分低頻正弦疊合成似隨機曲線，
     產生極微幅的站姿重心微移，幅度由 idleSwayAmp 控制 */
  _updateIdleSway(delta) {
    this._swayT += delta;
    const t = this._swayT;
    const amp = this.idleSwayAmp;
    const twoPi = 2 * Math.PI;
    const x = amp * (0.55 * Math.sin((twoPi * t) / 11.3 + 0.7) + 0.45 * Math.sin((twoPi * t) / 7.9 + 2.3));
    const y = amp * 0.4 * Math.sin((twoPi * t) / 15.7 + 3.0);
    const z = amp * (0.7 * Math.sin((twoPi * t) / 13.1 + 1.2) + 0.3 * Math.sin((twoPi * t) / 6.7 + 0.4));
    this._clampedAdd(P.ParamAngleX, x);
    this._clampedAdd(P.ParamAngleY, y);
    this._clampedAdd(P.ParamAngleZ, z);
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
    this._lastTapAt = 0;
    this.lastTime = performance.now() / 1000;
    this.modelRel = null;
    this.loadingRel = null;
    this.overlay = document.getElementById('overlay');
    this.statusEl = document.getElementById('conn-status');
    this.modelSelect = document.getElementById('model-select');
    this.tabHidden = document.hidden;
    this.autoBreath = true; // 呼吸晃動開關（切換模型時沿用）
    this.autoBlink = true;  // 自動眨眼開關
    this.idleSway = true;   // 自然搖擺開關（切換模型時沿用）
    this.breathAmp = 1;     // 呼吸角度幅度倍率（滑桿控制）
    this.breathFreq = 1;    // 呼吸週期頻率倍率（>1＝變快）
    this.swayAmp = 1;       // 自然搖擺幅度倍率
    this.lookFollow = true; // 視線跟隨游標（切換模型時沿用）
    this.lookFlipX = false; // 視線水平反轉（人物轉向與滑鼠相反時開啟）
    this.lookFlipY = false; // 視線垂直反轉
    this.tapReact = true;   // 點擊人物觸發反應（切換模型時沿用）

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
    // 視線跟隨綁在 window：游標離開畫布（甚至移到 UI 上）時仍能驅動視線
    window.addEventListener('pointermove', (e) => this._pointerMove(e));
    window.addEventListener('pointerup', (e) => this._pointerUp(e));
    window.addEventListener('pointercancel', (e) => this._pointerUp(e));
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
    try {
      this.canvas.setPointerCapture(e.pointerId);
    } catch (err) {
      // 合成事件或已釋放的指標可能無法捕捉，不影響座標追蹤
    }
    this._pressX = e.clientX;
    this._pressY = e.clientY;
    this._pressAt = performance.now() / 1000;
    this._updateDrag(e);
  }

  _pointerMove(e) {
    this._updateDrag(e);
  }

  _pointerUp(e) {
    const now = performance.now() / 1000;
    if (!this._pressAt) return;
    const dx = Math.abs(e.clientX - this._pressX);
    const dy = Math.abs(e.clientY - this._pressY);
    const dt = now - this._pressAt;
    this._pressAt = 0;
    // 快速且幾乎沒移動的點擊：依點擊部位觸發對應動作（本地防連點）
    if (this.tapReact && this.userModel && dx <= 10 && dy <= 10 && dt <= 0.3) {
      if (now - this._lastTapAt >= 0.6) {
        this._lastTapAt = now;
        const rect = this.canvas.getBoundingClientRect();
        const lx = clamp(((e.clientX - rect.left) / rect.width) * 2 - 1, -1, 1);
        const ly = clamp(((e.clientY - rect.top) / rect.height) * 2 - 1, -1, 1);
        const zone = tapZone(lx, ly);
        if (zone === 'chestTouch') {
          this.userModel.triggerChestBlush();
        } else if (zone) {
          this.userModel.applyMotionCmd({ name: zone });
        }
      }
    }
  }

  _updateDrag(e) {
    const rect = this.canvas.getBoundingClientRect();
    // 畫面座標 → 邏輯座標（-1..1，左上為原點）；游標離開畫布時依樣運算並夾到 ±1
    const w = rect.width;
    const h = rect.height;
    if (w <= 0 || h <= 0) return;
    this.dragX = clamp(((e.clientX - rect.left) / w) * 2 - 1, -1, 1);
    this.dragY = clamp(((e.clientY - rect.top) / h) * 2 - 1, -1, 1);
    if (this.userModel && this.lookFollow) {
      // 水平／垂直反轉開關：改變傳入 look 的拖拽符號（不更動 look 參數本身）
      this.userModel.setDragging(
        this.lookFlipX ? -this.dragX : this.dragX,
        this.lookFlipY ? -this.dragY : this.dragY
      );
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
      userModel.modelKey = rel.split('/')[0];
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

      // 呼吸與自然搖擺：以 clamp-add 寫入，確保參數值不跨越範圍界限，
      // 避免 repeat 參數在貼邊值上疊加而 wrap 到對向極限（造成大幅跳變）。
      userModel._breathAmp = this.breathAmp;
      userModel._breathFreq = this.breathFreq;
      userModel.idleSwayAmp = this.swayAmp;

      // 視線跟隨
      const pi = pid;
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

      // 層一：載入 model3.json Motions 指定的原生動作曲線
      await this._loadNativeMotions(userModel, setting, dir);

      this.userModel = userModel;
      this.modelRel = rel;
      // 套用目前微調參數（呼吸幅度／頻率、搖擺開關與幅度）
      userModel.idleSwayOn = this.idleSway;
      userModel._breathAmp = this.breathAmp;
      userModel._breathFreq = this.breathFreq;
      userModel.idleSwayAmp = this.swayAmp;
      userModel.lookFollow = this.lookFollow;
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

  /* 載入模型自帶的 motion3：解析曲線並以檔案主檔名建立索引 */
  async _loadNativeMotions(userModel, setting, dir) {
    userModel.nativeMotions = [];
    userModel.nativeMotionsById = {};
    const groupCount = setting.getMotionGroupCount();
    for (let g = 0; g < groupCount; g++) {
      const group = setting.getMotionGroupName(g);
      const count = setting.getMotionCount(group);
      for (let i = 0; i < count; i++) {
        const file = setting.getMotionFileName(group, i);
        try {
          const buf = await this._fetchArrayBuffer(`/model/${encRel(dir)}${encFile(file)}`);
          const json = JSON.parse(new TextDecoder().decode(buf));
          const meta = json.Meta || {};
          const id = (file.split('/').pop() || file).replace(/\.motion3\.json$/i, '');
          const rec = {
            id,
            group,
            file,
            dur: Number(meta.Duration) > 0 ? Number(meta.Duration) : 1,
            loop: !!meta.Loop,
            fadeIn:
              Number(setting.getMotionFadeInTimeValue(group, i)) > 0
                ? Number(setting.getMotionFadeInTimeValue(group, i))
                : 0.3,
            fadeOut:
              Number(setting.getMotionFadeOutTimeValue(group, i)) > 0
                ? Number(setting.getMotionFadeOutTimeValue(group, i))
                : 0.3,
            curves: (json.Curves || []).map((c) => ({
              id: c.Id,
              target: c.Target,
              pts: softDecodeSegments(c.Segments || []),
            })),
          };
          userModel.nativeMotions.push(rec);
          userModel.nativeMotionsById[id] = userModel.nativeMotions.length - 1;
        } catch (err) {
          console.warn('原生動作載入失敗：', group, i, file, err);
        }
      }
    }
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

function tpGroup(title, collapsible) {
  const box = document.createElement('div');
  box.className = 'tp-group';
  const h = document.createElement('div');
  h.className = 'tp-title';
  h.textContent = title;
  const btns = document.createElement('div');
  btns.className = 'tp-btns';
  box.appendChild(h);
  box.appendChild(btns);
  if (collapsible) {
    box.classList.add('tp-collapse');
    h.title = '按一下收合／展開';
    h.addEventListener('click', () => btns.classList.toggle('hidden'));
  }
  return { box, btns };
}

/* 滑桿列：label＋range＋目前數值；輸入時呼叫 apply 套用 */
function tpSlider(label, get, set, min, max, step, fmt, apply) {
  const row = document.createElement('div');
  row.className = 'tp-slider';
  const lab = document.createElement('span');
  lab.className = 'tp-slider-label';
  lab.textContent = label;
  const inp = document.createElement('input');
  inp.type = 'range';
  inp.min = String(min);
  inp.max = String(max);
  inp.step = String(step);
  inp.value = String(get());
  const disp = document.createElement('span');
  disp.className = 'tp-slider-val';
  const render = () => {
    disp.textContent = fmt ? fmt(get()) : String(get());
  };
  inp.addEventListener('input', () => {
    set(Number(inp.value));
    if (apply) apply();
    render();
  });
  row.appendChild(lab);
  row.appendChild(inp);
  row.appendChild(disp);
  render();
  return row;
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

  // 互動（可收合）：滑鼠與人物互動開關
  const inter = tpGroup('互動', true);
  inter.btns.appendChild(
    tpToggle('視線跟隨', () => app.lookFollow, (v) => {
      app.lookFollow = v;
      if (app.userModel) app.userModel.lookFollow = v;
    })
  );
  inter.btns.appendChild(
    tpToggle('點擊反應', () => app.tapReact, (v) => {
      app.tapReact = v;
    })
  );
  inter.btns.appendChild(
    tpToggle('水平反轉', () => app.lookFlipX, (v) => {
      app.lookFlipX = v;
    })
  );
  inter.btns.appendChild(
    tpToggle('垂直反轉', () => app.lookFlipY, (v) => {
      app.lookFlipY = v;
    })
  );
  const note = document.createElement('div');
  note.className = 'tp-note';
  note.textContent = '游標離開視窗後視線停在最後方向（瀏覽器限制）。';
  inter.btns.appendChild(note);
  body.appendChild(inter.box);

  // 微調（可收合）：自然搖擺開關＋呼吸／搖擺幅度與頻率滑桿
  const tune = tpGroup('微調（呼吸與搖擺）', true);
  tune.btns.appendChild(
    tpToggle('自然搖擺', () => app.idleSway, (v) => {
      app.idleSway = v;
      if (app.userModel) app.userModel.idleSwayOn = v;
    })
  );
  const syncTune = () => {
    if (!app.userModel) return;
    app.userModel._breathAmp = app.breathAmp;
    app.userModel._breathFreq = app.breathFreq;
    app.userModel.idleSwayAmp = app.swayAmp;
  };
  tune.btns.appendChild(
    tpSlider('呼吸幅度', () => app.breathAmp, (v) => { app.breathAmp = v; }, 0, 1.5, 0.05, (v) => Math.round(v * 100) + '%', syncTune)
  );
  tune.btns.appendChild(
    tpSlider('呼吸頻率', () => app.breathFreq, (v) => { app.breathFreq = v; }, 0.5, 2, 0.05, (v) => v.toFixed(2) + '×', syncTune)
  );
  tune.btns.appendChild(
    tpSlider('搖擺幅度', () => app.swayAmp, (v) => { app.swayAmp = v; }, 0, 1.5, 0.05, (v) => Math.round(v * 100) + '%', syncTune)
  );
  body.appendChild(tune.box);

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

// bundle 版本標記：重新整理後若看不到此版號，代表頁面仍在使用舊版 JavaScript
const BUILD_TAG = 'build-20260830-1550';
const verEl = document.getElementById('bundle-ver');
if (verEl) {
  verEl.textContent = BUILD_TAG;
  verEl.title = '已載入版號：' + BUILD_TAG;
}

const app = new ViewerApp(canvas);
app._resize();
initTestPanel(app);
window.__app = app; // 供頁面測試／外部腳本存取
app.fillModels();