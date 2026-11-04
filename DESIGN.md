---
name: 城配续航 · 网站与配送工作台
description: 圆润中文、水润玻璃与可核对的配送业务。
colors:
  forest: "#173f35"
  green: "#28644e"
  ink: "#193d34"
  muted: "#536e64"
  canvas: "#edf4ef"
  paper: "#fff"
  lime: "#d8edb9"
  line: "#ccded4"
  glass: "rgba(255,255,255,.7)"
  glass-line: "rgba(255,255,255,.92)"
  workspace-green: "#285c4b"
  workspace-ink: "#173e35"
  workspace-muted: "#526b63"
  workspace-canvas: "#eff5f1"
  workspace-line: "rgba(68,103,88,.17)"
  mist-blue: "#7299ad"
  jade: "#dfeee4"
  warm-apricot: "#f0dfcd"
  warning-ink: "#784e32"
  warning-surface: "#faeee3"
typography:
  display:
    fontFamily: "'Resource Han Rounded', 'YouYuan', 'Microsoft YaHei UI', sans-serif"
    fontSize: "clamp(44px,4.5vw,66px)"
    fontWeight: 650
    lineHeight: 1.3
    letterSpacing: "-.025em"
  headline:
    fontFamily: "'Resource Han Rounded', 'YouYuan', 'Microsoft YaHei UI', sans-serif"
    fontSize: "38px"
    fontWeight: 650
    lineHeight: 1.4
  title:
    fontFamily: "'Resource Han Rounded', 'YouYuan', 'Microsoft YaHei UI', sans-serif"
    fontSize: "22px"
    fontWeight: 650
    lineHeight: 1.45
  body:
    fontFamily: "'Resource Han Rounded', 'YouYuan', 'Microsoft YaHei UI', sans-serif"
    fontSize: "16px"
    fontWeight: 450
    lineHeight: 1.65
  site-body:
    fontFamily: "'Resource Han Rounded', 'YouYuan', 'Microsoft YaHei UI', sans-serif"
    fontSize: "17px"
    fontWeight: 450
    lineHeight: 1.75
  label:
    fontFamily: "'Resource Han Rounded', 'YouYuan', 'Microsoft YaHei UI', sans-serif"
    fontSize: "14px"
    fontWeight: 450
  metric:
    fontFamily: "'Resource Han Rounded', 'YouYuan', 'Microsoft YaHei UI', sans-serif"
    fontSize: "28px"
    fontWeight: 650
    lineHeight: 1.45
rounded:
  input: "13px"
  control: "15px"
  site-action: "16px"
  card: "24px"
  panel: "26px"
  feature: "28px"
  hero: "32px"
spacing:
  compact: "8px"
  control-gap: "12px"
  field-gap: "18px"
  section-gap: "20px"
  panel: "24px"
  site-gutter: "48px"
components:
  button-primary:
    backgroundColor: "{colors.workspace-green}"
    textColor: "{colors.paper}"
    rounded: "{rounded.control}"
    padding: "10px 15px"
    height: "44px"
    typography: "{typography.body}"
  button-secondary:
    backgroundColor: "rgba(255,255,255,.65)"
    textColor: "{colors.workspace-ink}"
    rounded: "{rounded.control}"
    padding: "10px 15px"
    typography: "{typography.body}"
  input:
    backgroundColor: "#ffffffb8"
    textColor: "{colors.workspace-ink}"
    rounded: "{rounded.input}"
    padding: "10px 12px"
    typography: "{typography.body}"
  stage-navigation:
    backgroundColor: "#e2ece570"
    rounded: "20px"
    padding: "6px"
  selection-chip:
    backgroundColor: "#dfece4"
    textColor: "#385f4d"
    rounded: "10px"
    padding: "5px 9px"
    typography: "{typography.label}"
  glass-card:
    backgroundColor: "{colors.glass}"
    rounded: "{rounded.card}"
    padding: "24px"
  agent-composer:
    backgroundColor: "#ffffffc9"
    rounded: "{rounded.panel}"
    padding: "17px"
---

# Design System: 城配续航 · 网站与配送工作台

## Overview

**Creative North Star: "圆润清透的配送工作空间"**

以圆润、清楚、轻盈的中文界面承载配车、路线、装载、费用与包装回收。墨绿建立品牌和操作重点，玉绿、雾蓝与暖杏铺出低色差背景，透明层与柔和阴影提供层次。官网留出阅读空间，工作台保留业务密度。

文案用简短自然的中文，按钮直接写动作，状态直接写事实。首页的流程图与对话是明确标注的示意；工作台中的事实、预测、候选与人工确认各有文字说明。视觉风格不能把模拟变成业绩，也不能把选中变成确认。

**Key Characteristics:**
- 自托管中文圆体与清晰字号
- 低色差的墨绿、玉绿、雾蓝和暖杏
- 圆角玻璃面板与适量按压反馈
- 官网阅读空间与工作台操作密度分工

2026-11-04：按用户已确认的持久风格方向更新。本文件描述现有实现；共享样式以 `web/rounded-theme.css` 为源，官网使用 `web/site.css`，工作台按 `web/application.css` → `web/workspace.css` 叠加。颜色前置元数据同时保留共享值和工作台覆盖值，不能将两者混为同一色值。产品事实以 `PRODUCT.md` 为准。

## Colors

低色差浅色背景承托墨绿正文和主操作，柔和色块服务业务分组与路线识别。

### Primary

- 深林绿（forest）：官网品牌和主要入口。
- 墨绿（green / workspace-green）：操作、选中、输入光标与工作台主按钮；后者为工作台覆盖值。

### Secondary

- 玉绿（jade）：助手标记与轻量上下文背景。
- 草木浅绿（lime）：保留共享强调色，避免铺成高饱和大底。
- 雾蓝（mist-blue）：首页回收线路；浅蓝透明背景用于助手解释区。

### Tertiary

- 暖杏（warm-apricot）：停止按钮等少量操作提示。
- 暖棕与浅杏（warning-ink / warning-surface）：错误、超限和未安排提醒，必须配文字。

### Neutral

- 绿墨文字（ink / workspace-ink）与灰绿说明（muted / workspace-muted）。
- 浅绿底（canvas / workspace-canvas）、纸白（paper）、玻璃白（glass）和亮边（glass-line）。
- 共享浅绿边界（line）与工作台半透明边界（workspace-line）。

**The Fact Label Rule.** 状态必须有可读文字；颜色、选中和动画不能代替业务事实或人工确认。

## Typography

**Display Font / Body Font:** Resource Han Rounded，回退 YouYuan、Microsoft YaHei UI、sans-serif。JSON 编辑器独立使用 Consolas、monospace。

字体自托管于 `web/fonts/`，两份 WOFF2 保留完整30,887字符，共9,977,316字节（约9.52MiB），随附 SIL Open Font License。源 Regular 的实际字重为400，页面正文以 CSS 450调用；源 Bold 的实际字重为700，标题以 CSS 650调用。`@font-face` 匹配区间分别为400–550和600–900；这是静态字体的匹配映射，不是可变字体或真实中间字重。

### Hierarchy

- Display：官网主标题使用前置 display 标记；1100px以下48px、760px以下45px。
- Headline：官网分区标题38px，移动31px；工作台主标题28px，移动25px。欢迎空态为34px，移动29px。
- Title：工作台区块22px，次级标题19px；字重650。
- Body：工作台基础16px，官网基础17px；对话正文16px、行高1.85。正文容器通常限制72ch。
- Label：表头、字段说明、来源、状态等辅助信息14–15px；表单输入16px。部分业务解释为14–15px，不能将基础16px误写为所有段落都16px。
- Metric：摘要28px，移动概览26px；工作台采用等宽数字对齐。

**The Readable Type Rule.** 正文以16px为基准，辅助文字不得低于14px；不要通过缩小文字解决溢出。

## Layout

官网内容容器最大1376px、桌面横向内边距48px；1100px以下32px，760px以下22px。桌面首屏为0.9:1.1双栏，移动单列；分区上下留白88px，移动58px。官网导航在760px及以下折叠。

工作台桌面顶栏82px；主体为230px批次栏、弹性业务区和390px助手栏，三个区域独立滚动，输入器固定在助手栏底部。1700px及以上助手栏410px；1450px及以下表单和摘要降低列数。1200px及以下批次栏220px、助手变为可收起浮层；850px及以下步骤折行、详情降低列数；720px及以下为74px粘性顶栏，批次列表转为上方横向滚动卡片，主区按文档流展开，助手保留可收起浮层。650px及以下矮视口压缩助手内边距和输入高度。

表格在自身容器横滚。工作台拓扑 SVG 最小宽600px，在图形容器内部横滚；官网能力图最小宽680px。图形坐标表达业务关系，不声称真实地理位置。不得通过整页横向溢出保留桌面密度。

## Elevation & Depth

浅色径向背景、半透明白层、亮边、模糊和扩散阴影共同形成水润玻璃感。共享玻璃阴影为 `0 18px 48px -22px rgba(31,77,59,.24)`；官网导航模糊22px，主图24px，助手区20px；工作台顶栏24px、侧栏22px、助手栏28px。官网对不支持 backdrop-filter 的环境提供实色浅底。组件详情保留在 sidecar，不把阴影或断点加入前置 token schema。

玻璃层仍须优先保障文字可读性。现有截图与浏览器检查不是所有透明层叠加情形的完整对比度认证。

## Shapes

大面板以24–32px圆角为主：业务卡24px、共享面板26px、官网重点28–32px。输入框13px，常规按钮15px，官网主按钮16px。上下文胶囊10px、状态标签11px和对话尾角9px是已实现的局部例外；移动业务面板22px。圆角围合任务与输入，不给每行文字增加独立卡片。

## Components

- 主要按钮：工作台墨绿底白字，最小44px高；官网入口最小52px高。次要按钮用半透明白，安静操作用透明底。焦点可见，禁用态同时保留可理解的状态。
- 输入框：圆润浅白底、绿色边界；默认16px文字和至少44px高度。JSON编辑器保留14px等宽字体。
- 阶段导航：浅绿轨道与白色选中项，使用 `aria-current=step`；阶段选择表示导航，不表示完成。
- 业务卡与拓扑：亮边玻璃底，摘要、路线和选择详情分层；路线节点支持键盘聚焦和选择。物理装载图仅从已生成的箱体坐标绘制；缺少配置时显示未核验。
- 上下文胶囊：标注当前仓库、车辆或门店，长文本换行，不挤压输入器。
- 助手输入器：26px圆角，聚焦时边界和阴影变化。对话与输入器分离，普通刷新不清空未发送文字；过程展示实际事件，规则草稿仍需人工审阅。
- 动效：官网路线虚线 routeFlow 为7秒配送、10秒反向回收，小车仅一次1.2秒入场；官网按钮悬浮上移3px，按压下移1px并缩放.98；工作台按压缩放.97，常规状态过渡约.2秒。运行提示仅在真实运行状态呼吸。`prefers-reduced-motion: reduce` 关闭动画和过渡，并恢复自动滚动。

验收记录（2026-11-04）：`outputs/review-20261104/browser-verification.json` 覆盖首页及移动导航、示例计算与车辆选择、未发送输入保留、人工确认、模拟签收、锁定与刷新持久化、移动助手切换、官网及资料核对和旧入口加载；记录无 JavaScript 错误，六个已记录视图无整页横向溢出、无低于14px的已检查可见文字、字体已载入。六张截图为 home-desktop、home-mobile、workspace-desktop、workspace-execution、workspace-mobile、agent-mobile。独立 finish review 结论为 ship，无 material findings。

验收限制：未做透明层全部组合的完整对比度认证、真实手机软键盘覆盖检查或本轮真实模型 API 调用（记录 model_calls=0）。设计检测器使用正则降级；其旧 DESIGN 告警不代表当前实现失败，也不等同完整选择器或计算样式审计。历史 API 联调事实见 PRODUCT.md，不冒充本轮验证。

## Do's and Don'ts

- Do 使用简短中文动作和明确的业务状态。
- Do 保留文字标签、键盘焦点、内部滚动与减少动画支持。
- Do 将示意、预测、模拟、候选与实际事实分别标明。
- Do 复用共享圆体与现有色阶，同时保留工作台覆盖值。

- Don't 为追求紧凑把辅助文字缩到14px以下。
- Don't 用装饰动效表示计算进度或真实配送。
- Don't 隐藏人工确认、规则审阅或物理装载未核验状态。
- Don't 将旧版尖角、小字号或系统字体重新当成默认风格。
