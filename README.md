# 城配续航 LogiReflow

城市配送智能配载与异常重排原型。**路线可行，还需要装得下、卸得出；出发后的调整必须保留原坐标、货物归属与执行事实。**

参赛者：陈祉好｜东北财经大学。

成员简介：围绕经济学问题开展研究资料整理，参与过Python/Jupyter数据分析实践，并借助AI工具推进需求梳理、方案设计与原型验证。本项目关注业务约束、可执行方案与验证证据的结合。

传化 × 动势“AI重构产业·架构师大赛”，AI＋物流赛道。私密仓库：[greytender/logi-reflow](https://github.com/greytender/logi-reflow)。全部样例为构造数据、人工或脚本模拟回执，没有真实派单或企业收益证明。仓库同步不等于正式比赛提交，最新状态见[比赛核对](docs/比赛核对.md)。

## 打开与演示

Windows安装Python 3.10+并加入PATH后，双击 **打开城配续航.cmd**，访问 **http://127.0.0.1:8766/**。也可在项目目录运行：

```powershell
python server.py
```

应用运行仅需Python标准库，无需npm或第三方Python包；前端使用原生JavaScript与SVG。服务只绑定127.0.0.1。启动器优先打开Chrome；没有Chrome时会按现有逻辑回退系统浏览器，指定Chrome演示请先安装Chrome。本轮官网核查只使用用户指定Chrome。

端口冲突时执行 `python server.py --port 8767`，然后打开对应地址。独立运行可加 `--database outputs/reviewer-test.sqlite3`，避免使用原运行数据库。

唯一应用包含“能力演示、交互模拟、专业工作台、方法与边界”；旧链接仅跳转到同一应用。首页“开始完整演示”覆盖六店十二箱、联合路线与装载、十八条脚本模拟回执、回仓及异常交接。具体三分钟顺序见[评审演示说明](docs/评审演示说明.md)。**无需AI密钥即可展示核心能力。**

## 算法能力与反例

| 构造情境 | 当前结果 | 正确解释 |
|---|---|---|
| 重量/体积可行但几何不可行 | 48元方案装不下，117元联合方案通过核验 | 可执行性有代价，48元不能作为可执行降本基线 |
| 出发后重排 | 原摆放下A→C→B卸不出；A→B→C可执行，保留45分钟迟到，均38元 | 原坐标约束不能被更快的路线绕过 |
| 易碎/堆叠限制 | 部分订单未安排，保留失败且不允许采用不完整候选 | 不隐藏失败 |

路线层使用多起点插入、订单移位和有限候选搜索；装载层使用逆配送顺序、三种有限排序与箱体面角点构造。`loading_audit.py`独立重算坐标、支撑链和装卸中间状态。**不保证全局最优，未实现ALNS或强化学习；启发式未找到不等于数学无解。**

AI仅负责事件字段提取和补问，服务端校验后由人核对，本地算法核验候选，再单独核对采用。不会自动派单。可选DeepSeek调用需本人密钥并产生API用量；设置不会自动调用。临时密钥服务端仅内存保留一小时，重启失效；浏览器仅收HttpOnly不透明会话标识。生产权限与远程部署安全体系未完成。

## 状态与范围

主应用交互使用独立临时状态，闲置两小时或服务重启失效，刷新需重新开始，可先导出。专业工作台使用SQLite保存场景/版本，保留CSV导入、连续收货变化、普通CSV模式最多两个仓库紧急单、分步人工模拟回执、只读历史与交接、独立故障试算及联合装载。

固定装载模式冻结尺寸、坐标与原货归属，改序只核验原摆放；整单签收后才移除箱体。普通CSV模式只核验总重量/体积，不承诺3D可行。出发后不能切换模式绕过约束。候选不改变执行，推进执行使旧候选失效，已签收事实不能撤回。

尚未实现或验证：固定空间新货插入、连续故障、温控、轴荷、多箱共同支撑、侧门与现场可达性、真实TMS/WMS/GPS/司机接口、完整企业成本与收益。固定装载最多80箱，搜索预算可能触顶；30店CSV复现不等于数百SKU的3D规模证明。

## 架构、输入与复现

- `web/index.html`、`app.css`、`app-shell.js`：单一应用；`workspace.html`、`cargo.html`为应用内专业模块。
- `server.py`：本机HTTP/API；`scenario_workspace.py`：临时交互；`continuous.py`、`store.py`：持久化状态、版本及修订保护。
- `normal.py`、`matrix_dispatch.py`、`urgent.py`：路线与CSV调度；`loading.py`、`loading_audit.py`、`fixed_loading.py`：联合装载、独立核验与原坐标约束。
- `ai_events.py`、`csv_ai_events.py`、`session_keys.py`：可选模型调用、事件合同与临时密钥。
- `examples/scenario-six`、`fixed-loading-three`、`loading-small`、`pilot-30`：构造输入。CSV订单/车辆/有向道路/设置与明确箱规、车厢的loading.json，见[模型说明](docs/业务与模型.md)及[输入合同](docs/企业离线试算入口.md)。

```powershell
$env:NO_PROXY='127.0.0.1,localhost,::1'
python -m unittest discover -s tests -q
python scenario_demo.py
python fixed_loading_benchmark.py
python loading_benchmark.py
python reproduce_csv_checkpoint.py
```

上述复现不调用AI，使用临时数据库或输出目录，不代表实际业务。打包命令：`python build_checkpoint.py --output outputs/城配续航网页应用-20261012.zip --label LogiReflow-20261012 --verify`。交付排除运行数据库、密钥、日志、缓存与outputs。

## 验证证据

之前网页改造已有185项回归、13组后台真实Chrome检查通过，见[原验证记录](evidence/网页应用改造验证.md)、[机器记录](evidence/app-browser-verification.json)与`evidence/tests-review-ui.txt`。本轮验证另记[交付验证](evidence/文档恢复与交付验证-20261012.md)，不将旧记录当本轮重跑。

真实截图：[首页](evidence/app-home-desktop.png)、[手机](evidence/app-home-mobile.png)、[完整演示](evidence/app-complete-demo.png)、[候选核对](evidence/app-candidate-review.png)。28组构造异常、56份独立核算方案保留无改善、取舍与不完整结果；不证明企业收益或原创算法。

[作品方案](docs/作品方案与评审展示.md)｜[提交清单](docs/提交材料清单.md)｜[方法来源](docs/智能配载方法筛选与实现.md)。带“历史”的记录仅供追溯，其旧入口、数量与未实现状态不作为当前说明。

官网提交指导与物流正文已于2026-10-12用Tabbit实际复核。本人确认截止为当日18:00（北京时间），具体[初赛登记表](https://v.wjx.cn/vm/rmEuv50.aspx#)已由本人提交，本轮通过Tabbit实际读到答卷已提交的完成页；DsChHack/write协作邀请已发送、待接受。官网知识产权归属条款与来源边界见[比赛核对](docs/比赛核对.md)，助手未代点最终提交或独立接受条款。
