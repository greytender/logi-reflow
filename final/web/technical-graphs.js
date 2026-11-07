/* Executable diagram definitions. Each node/edge is an explicit technical step. */
'use strict';
const N=(id,c,r,label,type='process',tone='green')=>({id,c,r,label,type,tone});
const E=(from,to,label='',kind='',ports='',via=[])=>({from,to,label,kind,ports,via});
const G=(id,title,lanes,nodes,edges,source,status='implemented')=>({id,title,lanes,nodes,edges,source,status});
window.technicalGraphs=[
G('orders','原始 Excel → 可追溯中央仓任务',['原始证据','映射与单位','业务归并','调度任务'],[
 N('a',0,0,'Excel 原始字节\n116 行 / SHA-256','database'),N('b',1,0,'文件摘要命中？\n中央仓专用口径','decision'),N('c',2,0,'通用字段映射\n其他文件逐项确认','process','amber'),N('d',3,0,'原值 / 公式 / 坐标\n不可覆盖','database'),
 N('e',0,1,'门店 C / 地址 D\n常温 E:G · 冷链 H:J'),N('f',1,1,'32 行空冷链\n本批确认按零'),N('g',2,1,'冷链 10³ cm³ / 件\nV = q × 0.001 m³'),N('h',3,1,'行级证据包\nsource_refs / volume_basis','database'),
 N('i',0,2,'同名门店标识\nSTORE_ + hash(name)'),N('j',1,2,'常温 / 冷链汇总\n原行号与地址保留'),N('k',2,2,'116 笔 → 103 门店\n整店配送 / 不拆单'),N('l',3,2,'中央仓批次\ninput_revision / hash','database'),
 N('m',0,3,'车辆 / 道路 / 箱规\n回收 / 时间另行配置','process','amber'),N('n',1,3,'单位 / 完整性\n字段与取值核验'),N('o',2,3,'资料完整？','decision'),N('p',3,3,'进入联合配车\n原表可下载','terminal')
],[E('a','b'),E('b','c','否','no'),E('b','f','是'),E('c','d'),E('d','h'),E('e','f'),E('f','g'),E('g','h'),E('i','j'),E('j','k'),E('k','l'),E('h','l'),E('m','n'),E('n','o'),E('l','o'),E('o','p','是'),E('o','g','补充配置','feedback','top:bottom')], 'central_orders.configure / normalize · intake_review · intake_dispatch'),
G('architecture','联合调度系统架构与责任边界',['交互层','业务服务层','计算与核验层','数据与可视化'],[
 N('a',0,0,'final 调度工作台\n批次 / 车次 / 订单'),N('b',1,0,'HTTP / 同源校验\n任务与修订号'),N('c',2,0,'整店插入 / 车队搜索\nfleet_planning.solve'),N('d',3,0,'SQLite 批次库\n原输入 / 多版候选','database'),
 N('e',0,1,'小航聚合接口\n四家模型聚合接口','process','amber'),N('f',1,1,'工具调用 / 上下文\n不直接确认与派单'),N('g',2,1,'专项构造器\n冷链 / 几何 / 装卸'),N('h',3,1,'原始 Excel / 参数\n来源分层','database'),
 N('i',0,2,'调度员调整与核定\n显式人工动作','human'),N('j',1,2,'版本校验 / 原子提交\nguard → commit'),N('k',2,2,'独立核验\n覆盖 / 容量 / 动作回放'),N('l',3,2,'3D 场景协议\n箱体 + 时间 + 动作'),
 N('m',0,3,'驾驶 / 装卸动态播放\n旋转 / 剖切 / 选箱'),N('n',1,3,'版本绑定的场景 API\nplan_id + vehicle_id'),N('o',2,3,'学习与护栏试验入口\n独立试验入口','process','purple'),N('p',3,3,'企业 TMS / GPS / 设备\n交接协议 / 字段映射','process','amber')
],[E('a','b'),E('b','c'),E('c','d'),E('e','f','','api'),E('f','g'),E('g','k'),E('h','g'),E('i','j'),E('j','k'),E('k','l'),E('l','n'),E('n','m'),E('o','k','','feedback'),E('d','h'),E('j','p','交接契约','api')], 'launch.FinalHandler · multiwarehouse · operational_constraints · integration_contract'),
G('contract','统一输入契约与计算准入',['订单','运力与路网','专项参数','验证结果'],[
 N('a',0,0,'orders[]\n归仓 / 货品行 / 时窗','database'),N('b',1,0,'vehicles[]\n载重 / 容积 / 班次','database'),N('c',2,0,'operational_policy\n车厢 / 箱规 / 操作','database'),N('d',3,0,'统一单位与类型\nkg / m³ / cm / min'),
 N('e',0,1,'同店订单完整\n允许车辆集合'),N('f',1,1,'有向 legs[a>b]\n里程 / 行驶分钟'),N('g',2,1,'温区 / 冷箱内容\n临时区 / 回收几何'),N('h',3,1,'ID / 有限数值\n缺项与范围检查'),
 N('i',0,2,'门店 ≤ 128\n单仓 fleet 模式'),N('j',1,2,'车辆 ≤ 24\n每车一趟'),N('k',2,2,'箱体实体 / 构造预算\n160 / 8192 / 512 帧'),N('l',3,2,'全部满足输入契约？','decision'),
 N('m',0,3,'缺失字段与冲突\n保留原始来源','process','amber'),N('n',1,3,'人工补充 / 核对\n不猜造实际参数','human'),N('o',2,3,'canonicalize\n生成稳定输入快照'),N('p',3,3,'开始同仓求解','terminal')
],[E('a','e'),E('b','f'),E('c','g'),E('d','h'),E('e','i'),E('f','j'),E('g','k'),E('h','l'),E('i','j'),E('j','k'),E('k','l'),E('l','p','通过'),E('l','m','不通过','no','bottom:top',[[1170,450],[50,450]]),E('m','n'),E('n','o'),E('o','p')], 'application.canonicalize · multiwarehouse.canonicalize · validate_policy'),
G('grouping','整店不可拆与共享冷链箱成组分车',['门店需求','冷链箱关联','共箱连通分组','可用运力'],[
 N('a',0,0,'103 个合单门店\n每店恰好一次','database'),N('b',1,0,'冷链货品 → 箱 ID\n一个箱可服务多店'),N('c',2,0,'共享箱关系连接\n跨箱关联继续合并'),N('d',3,0,'形成不可拆组 G\n不能跨车分开'),
 N('e',0,1,'各店允许车辆 Aᵢ\n准入 / 运力归仓'),N('f',1,1,'冷箱允许车辆 Bⱼ\n设备 / 车厢约束'),N('g',2,1,'组允许集\nA(G)=∩Aᵢ∩Bⱼ'),N('h',3,1,'A(G) 为空？','decision'),
 N('i',0,2,'组内时窗优先\n也尝试反向顺序'),N('j',1,2,'组总重量 / 总体积\n含共享箱外形与自重'),N('k',2,2,'候选插入所有允许车\n保持组完整'),N('l',3,2,'NO_ELIGIBLE_VEHICLE\n待安排并保留原因','process','amber'),
 N('m',0,3,'共享箱最多 16 方件\n按内尺寸几何构造'),N('n',1,3,'最多 3 门店 / 箱\n本批配置非普适常量'),N('o',2,3,'逐车专项核验\n温控 + 装卸 + 回收'),N('p',3,3,'分组覆盖审计\n无漏店 / 无重复','terminal')
],[E('a','b'),E('b','c'),E('c','d'),E('e','g','','','right:left'),E('f','g'),E('d','h'),E('g','h'),E('h','l','是','no'),E('h','k','否'),E('i','j'),E('j','k'),E('m','n'),E('n','o'),E('k','o'),E('o','p')], 'warehouse_loading.shared_box_groups · central_orders.parameters'),
G('search','有界整组插入、专项筛选与补排',['候选种子','快速路线核算','昂贵专项检查','修复与终止'],[
 N('a',0,0,'空车路线集合\n固定本批可用车队','terminal'),N('b',1,0,'时窗优先 / 载荷优先\n均衡负载种子'),N('c',2,0,'满足共箱约束时\n保留规则种子'),N('d',3,0,'按种子序逐车求解'),
 N('e',0,1,'门店组正向 / 反向\n所有允许车与插入位'),N('f',1,1,'LRU 路线核算\n准入 / 时窗 / 容量'),N('g',2,1,'冷链与几何预筛\n减少昂贵复算'),N('h',3,1,'取高优先候选\nqualify 专项构造'),
 N('i',0,2,'迟到 → 费用 → 倒货\n→ 里程 → 返仓排序'),N('j',1,2,'完整专项通过？','decision'),N('k',2,2,'保留已核验路线\n从组尾移除失败组'),N('l',3,2,'待安排组补入\n前 6 候选 + 空车'),
 N('m',0,3,'40,000 快核 / 160 专项\nfinal 搜索上限 90 秒'),N('n',1,3,'全覆盖或预算耗尽？','decision'),N('o',2,3,'已通过路线 + 未排原因\n保留全部任务'),N('p',3,3,'整批独立核验\n有限候选搜索','terminal')
],[E('a','b'),E('b','c'),E('c','d'),E('d','h'),E('e','f'),E('f','g'),E('g','h'),E('h','j'),E('i','j'),E('j','k','否','no'),E('k','l'),E('j','n','是'),E('l','n'),E('m','n'),E('n','o','是'),E('n','e','否','feedback','left:left',[[30,390],[30,190]]),E('o','p')], 'fleet_planning.solve · proposals / model / repair · pilot.evaluator'),
G('screen','快核与预筛：缩小候选，不替代专项',['路线增量','时间与载荷','冷链与通道预筛','晋级 / 退回'],[
 N('a',0,0,'候选 seq + vehicle\n包含完整门店组','terminal'),N('b',1,0,'订单准入 / 同仓\n货量 / 班次'),N('c',2,0,'逐段行驶 + 等待\n基础服务 + 回收'),N('d',3,0,'快核可行？','decision'),
 N('e',0,1,'共享箱外体积 / 自重\n替代冷货裸算体积'),N('f',1,1,'开箱 + 搬倒服务下界\n冷链时限筛选'),N('g',2,1,'通道宽度与同规格槽位\n当前构造必要检查'),N('h',3,1,'预筛存在已知冲突？','decision'),
 N('i',0,2,'更新路线缓存\n相同 seq 不重复核算'),N('j',1,2,'冷链时限失败时\n可试整条反向路线'),N('k',2,2,'昂贵专项核验\n真实箱位与动作'),N('l',3,2,'候选退回 / 记录原因\n不宣称几何无解','process','amber'),
 N('m',0,3,'成本增量 + 载荷压力\n候选排序'),N('n',1,3,'仅剩余预算内\n进入布局构造'),N('o',2,3,'专项通过？','decision'),N('p',3,3,'保留可核对候选','terminal')
],[E('a','b'),E('b','c'),E('c','d'),E('e','f'),E('f','g'),E('g','h'),E('d','h','是'),E('d','l','否','no','right:right',[[1180,90],[1180,310]]),E('h','l','是','no'),E('h','k','否'),E('i','j'),E('j','k'),E('m','n'),E('n','o'),E('k','o'),E('o','p','是'),E('o','l','否','no')], 'warehouse_loading.operational_screen / operational_evaluator · fleet_planning.core'),
G('packing','三维装载构造与几何审计',['物理实体','候选位置','几何与承载','装车见证'],[
 N('a',0,0,'常温箱 / 共享箱\n回收包装 / 退货','database'),N('b',1,0,'车辆 L×W×H\n固定朝向 / 声明温区'),N('c',2,0,'按逆站序与箱体策略\n排序装车实体'),N('d',3,0,'初始已放集合为空','terminal'),
 N('e',0,1,'生成边界候选点\n箱沿 / 层高 / 通道'),N('f',1,1,'容器边界与温区\n0≤x,y,z 且不越界'),N('g',2,1,'三轴无相交\nAABB overlap = false'),N('h',3,1,'全底面支撑\n易碎 / 顶载约束'),
 N('i',0,2,'尾门取货 / 装车通路\n不允许穿过已有箱'),N('j',1,2,'车重 / 容积约束\n共享箱壳不可释放'),N('k',2,2,'找到合法位置？','decision'),N('l',3,2,'保存坐标 + 装车动作\nfrom / to / box_ids'),
 N('m',0,3,'packing_not_found\n当前有限构造未找到','process','amber'),N('n',1,3,'预算耗尽单独编码\n不等于无解','process','amber'),N('o',2,3,'还有未装箱体？','decision'),N('p',3,3,'departure 状态帧\n进入逐站装卸','terminal')
],[E('a','b'),E('b','c'),E('c','d'),E('d','h'),E('e','f'),E('f','g'),E('g','h'),E('h','k'),E('i','j'),E('j','k'),E('k','l','是'),E('k','n','预算','no'),E('k','m','否','no','left:top',[[620,330],[150,330]]),E('l','o'),E('o','p','否'),E('o','e','是','feedback','left:left',[[25,440],[25,190]])], 'operational_constraints._place / audit_state / _evaluate_route'),
G('cold','冷链内容交付与共享箱持续占位',['冷箱定义','内部装箱','在途与开箱','交付后状态'],[
 N('a',0,0,'箱体外形 58×31×36\n自重 4.5kg · 本批配置','database'),N('b',1,0,'内尺寸 47.5×24.5×29.5\n10cm 方件至多 16 件'),N('c',2,0,'温区包含 / 载重\n冷货与箱体关联'),N('d',3,0,'参数完整且兼容？','decision'),
 N('e',0,1,'设备启用时间 t₀\n不得晚于发车'),N('f',1,1,'到店等待 + 开箱\n开箱次数累计'),N('g',2,1,'交付时限与温区\n交付完成再核验'),N('h',3,1,'时限 / 次数通过？','decision'),
 N('i',0,2,'冷货交接 cold_open\n仅扣本站内容重量'),N('j',1,2,'剩余箱重\n自重 + 未交付内容'),N('k',2,2,'箱体 x,y,z / dx,dy,dz\n保持占位'),N('l',3,2,'共享箱继续随车\n回收容量不能借空壳'),
 N('m',0,3,'cold_duration / openings\n拒绝或尝试其他路线','process','amber'),N('n',1,3,'冷链临时区温区\n移箱必须再验证'),N('o',2,3,'全部内容交完？','decision'),N('p',3,3,'共享箱原尺寸返仓\n不自动消失','terminal')
],[E('a','b'),E('b','c'),E('c','d'),E('d','h','是'),E('e','f'),E('f','g'),E('g','h'),E('h','l','通过'),E('d','m','缺项','no','left:left',[[25,30],[25,440]]),E('h','m','失败','no','right:bottom',[[1170,190],[1170,520],[150,520]]),E('i','j'),E('j','k'),E('k','l'),E('l','o'),E('n','o'),E('o','p','是'),E('o','f','否','feedback','top:bottom')], 'validate_policy · cold_open · cold_duration · remaining_content_weight_kg'),
G('unload','逐站卸货与阻挡箱搬倒状态机',['到店','可达性判断','暂存与交付','恢复与离店'],[
 N('a',0,0,'到店 arrival\n先冷链后常温目标','terminal'),N('b',1,0,'目标箱在车内？','decision'),N('c',2,0,'尾门 / 顶部阻挡集合\n_blocking(target)'),N('d',3,0,'存在阻挡？','decision'),
 N('e',0,1,'允许搬倒？\n临时位数足够？','decision'),N('f',1,1,'逐件移出可达阻挡箱\n上层 / 靠门优先'),N('g',2,1,'临时地面几何与温区\n重量 / 支撑再次核验'),N('h',3,1,'rehandle_out 记录\n搬倒耗时与高风险数'),
 N('i',0,2,'unload / cold_open\n交付目标货品'),N('j',1,2,'immediate：即取即还\nstop_batch：同站暂存'),N('k',2,2,'恢复安全箱位\nrehandle_in + 时间'),N('l',3,2,'恢复后整车状态\n支撑 / 通路 / 载荷'),
 N('m',0,3,'阻挡 / 暂存 / 恢复失败\n明确失败码','process','amber'),N('n',1,3,'追加本站服务时长\n再算冷链交付完成'),N('o',2,3,'卸货动作审计通过？','decision'),N('p',3,3,'进入回收装车\npickup','terminal')
],[E('a','b'),E('b','c','是'),E('c','d'),E('d','h','是'),E('e','f','是'),E('f','g'),E('g','h'),E('h','i','','','bottom:top',[[1070,245],[150,245]]),E('d','i','无阻挡','','right:left',[[1180,90],[1180,270],[30,270],[30,330]]),E('i','j'),E('j','k'),E('k','l'),E('l','o'),E('m','n'),E('n','o'),E('o','p','是'),E('e','m','否','no'),E('b','e','缺失','no')], 'operational_constraints.remove_box / _blocking / restore_one / audit_actions'),
G('recovery','先卸后收与包装回收全过程容量',['本站交付后','回收实体化','三维放置','后续路段'],[
 N('a',0,0,'配送货物已卸\n共享冷箱仍在车','terminal'),N('b',1,0,'预计回收 cartons / totes\n来源独立保存','database'),N('c',2,0,'纸箱压平高度\nh = q × flat_height'),N('d',3,0,'周转箱套叠高度\nh = h₁+(q−1)Δh'),
 N('e',0,1,'包装重量累加\nq × 单件自重'),N('f',1,1,'生成回收实体\nPICKUP/store/type'),N('g',2,1,'配置封箱常温退货\n单独实体与来源'),N('h',3,1,'逐实体寻找安全箱位\n不得借用虚拟空余'),
 N('i',0,2,'加回收操作时间\nq × unit_minutes'),N('j',1,2,'整车边界 / 支撑\n不重叠 / 门口通路'),N('k',2,2,'载重 / 容积 / 实体数\n逐站动态峰值'),N('l',3,2,'回收后可行？','decision'),
 N('m',0,3,'保留未通过原因\npickup_spatial_not_found','process','amber'),N('n',1,3,'回收后 departure_stop\n记录箱位与时间'),N('o',2,3,'后续门店 / 最终返仓\n原箱壳与包装共存'),N('p',3,3,'现场实收另行登记\n规划值不等于事实','human')
],[E('a','b'),E('b','c'),E('c','d'),E('d','h'),E('e','f'),E('f','g'),E('g','h'),E('h','l'),E('i','j'),E('j','k'),E('k','l'),E('l','m','否','no','right:bottom',[[1180,330],[1180,520],[150,520]]),E('l','o','是'),E('n','o'),E('o','p')], 'pickup_geometry · packaging · _entity · pickup_spatial_not_found'),
G('timeline','道路、等待、装卸、冷链与返仓时间耦合',['到店时间','本站额外操作','交付口径','返仓与能量'],[
 N('a',0,0,'上一站完成时间 tᵢ₋₁\n出发 t₀ = shift_start','terminal'),N('b',1,0,'道路到达 rᵢ\nrᵢ=tᵢ₋₁+travelᵢ'),N('c',2,0,'收货开始 aᵢ\naᵢ=max(rᵢ,openᵢ)'),N('d',3,0,'等待 wᵢ\nwᵢ=aᵢ−rᵢ'),
 N('e',0,1,'搬倒移出 / 恢复\nmoves × move_minutes'),N('f',1,1,'冷链开箱与取货\nopen_count × minutes'),N('g',2,1,'门店基础服务\nservice_minutes'),N('h',3,1,'包装与退货处理\nunits × pickup_minutes'),
 N('i',0,2,'交付完成 fᵢ\naᵢ + 全部操作时长'),N('j',1,2,'window_rule\nfinish / arrival'),N('k',2,2,'硬窗 ≤ closeᵢ\n软迟到=max(0,t−dueᵢ)'),N('l',3,2,'冷链完成时刻\nfᵢ−activated≤valid'),
 N('m',0,3,'逐站递推至最后一店\n再加返仓道路时间'),N('n',1,3,'return_at≤shift_end\n回仓截止时间'),N('o',2,3,'声明能耗：d×kWh/km\n加预留 ≤ 可用电量'),N('p',3,3,'时间 / 能量全通过？\n失败返回候选搜索','decision')
],[E('a','b'),E('b','c'),E('c','d'),E('d','h'),E('e','f'),E('f','g'),E('g','h'),E('h','i','','','bottom:top',[[1070,245],[150,245]]),E('i','j'),E('j','k'),E('k','l'),E('i','m'),E('m','n'),E('n','o'),E('o','p'),E('l','p')], 'pilot.evaluator · operational_constraints._evaluate_route / readiness_issue'),
G('cost','计费目标、比较口径与公里来源',['费用输入','逐车核算','多目标排序','比较与交接'],[
 N('a',0,0,'同批车队 / 同合同\n同样装卸约束','database'),N('b',1,0,'140 元起步\n含 100 km · 本批'),N('c',2,0,'超出部分 2 元/km\n每店服务 15 元'),N('d',3,0,'单车费用 Cᵥ\n140+2max(0,d−100)+15n'),
 N('e',0,1,'未安排优先减少\n再看迟到与成本'),N('f',1,1,'路线排序：迟到 / 费用\n高风险搬倒 / 总搬倒'),N('g',2,1,'里程 / 返仓时间\n作为后续比较项'),N('h',3,1,'可行性与代价同时展示\n不隐藏新增车次'),
 N('i',0,2,'道路试算 distance\n本批构造有向矩阵'),N('j',1,2,'TMS billing km\n计费公里字段','process','amber'),N('k',2,2,'GPS actual km\n实驶公里字段','process','amber'),N('l',3,2,'三个字段独立保留\n分别记录来源'),
 N('m',0,3,'体积基线 8 车\n专项可行性另审','process','amber'),N('n',1,3,'当前专项候选 16 车\n103 门店 / 未排 0'),N('o',2,3,'同口径核验均通过？','decision'),N('p',3,3,'仅此时比较代价\n非实测节省结论','terminal')
],[E('a','b'),E('b','c'),E('c','d'),E('e','f'),E('f','g'),E('g','h'),E('d','h'),E('i','j','','api'),E('j','k','','api'),E('k','l'),E('m','n'),E('n','o'),E('l','p'),E('o','p','是')], 'business.fee · fleet_planning.route_score / rank · plan.summary'),
G('layout-search','有限多布局组合与同站暂存改良',['固定输入与护栏','布局组合','动作复核','保留见证'],[
 N('a',0,0,'固定路线 / 箱规 / 车队\n保留 legacy 对照','terminal','purple'),N('b',1,0,'legacy / wide_ties\nwide_lanes'),N('c',2,0,'cold_last / large_first\nheavy_first'),N('d',3,0,'最多 6 变体\n共享构造预算'),
 N('e',0,1,'fleet 默认 route_lanes\n同站 stop_batch 恢复'),N('f',1,1,'完整构造每个布局\n装车 → 全部站点 → 回仓'),N('g',2,1,'独立 audit_actions\n重新校验全部状态'),N('h',3,1,'构造且回放通过？','decision'),
 N('i',0,2,'记录失败码与检查数\n含预算耗尽','process','amber'),N('j',1,2,'候选质量排序\n迟到 / 高风险 / 搬倒'),N('k',2,2,'再比较返仓时间\n不放宽任何约束'),N('l',3,2,'首个可行停止 / 全组合\n按显式开关执行'),
 N('m',0,3,'无可行且预算耗尽\nSEARCH_BUDGET_EXHAUSTED','process','amber'),N('n',1,3,'保存 selected_variant\n实际布局 / 动作 / 帧','database','purple'),N('o',2,3,'保存完整布局与动作\n重构可能丢失见证'),N('p',3,3,'六变体为可选试验\nfleet 默认策略另行保留','terminal','purple')
],[E('a','b'),E('b','c'),E('c','d'),E('e','f'),E('f','g'),E('g','h'),E('d','h'),E('h','i','否','no','left:top',[[780,245],[150,245]]),E('h','l','是'),E('j','k'),E('k','l'),E('l','n','','','bottom:top',[[1070,390],[450,390]]),E('i','m'),E('n','o'),E('o','p')], 'search_route_layouts · LAYOUT_VARIANTS · HANDLING_VARIANTS · audit_actions','optional'),
G('audit','独立核验与三重证据闭环',['输入与覆盖','路线复算','动作回放','整批准出'],[
 N('a',0,0,'不可变输入快照\ninput_sha256','database'),N('b',1,0,'门店归仓 / 车辆归仓\n全部任务恰好出现一次'),N('c',2,0,'重新核算路线指标\n费用 / 时窗 / 载荷'),N('d',3,0,'批次基础审计\npilot.audit'),
 N('e',0,1,'保存的摆放与动作\n不是重新猜测箱位','database'),N('f',1,1,'逐动作 ID 与 from/to\n移出 / 移回 / 交付'),N('g',2,1,'逐状态几何 / 重量\n临时区 / 支撑 / 温区'),N('h',3,1,'独立回放一致？','decision'),
 N('i',0,2,'冷链完成 / 开箱次数\n返仓 / 能量核验'),N('j',1,2,'共箱不拆 / 无丢失\n卸货与回收实体守恒'),N('k',2,2,'所有选中路线通过？','decision'),N('l',3,2,'保留约束失败证据\n禁止伪装完整通过','process','amber'),
 N('m',0,3,'预算耗尽 / 资料缺项\n保留未知状态','process','amber'),N('n',1,3,'整批 audit.passed\nserved / unserved'),N('o',2,3,'计划 ID + 修订号\n绑定所有下游视图'),N('p',3,3,'交调度员核对\n不得自动核定','human')
],[E('a','b'),E('b','c'),E('c','d'),E('e','f'),E('f','g'),E('g','h'),E('d','h'),E('h','l','否','no'),E('i','j'),E('j','k'),E('h','k','是'),E('k','n','是'),E('k','l','否','no'),E('m','n'),E('n','o'),E('o','p')], 'multiwarehouse.audit · pilot.audit · evaluate_plan · audit_actions'),
G('changes','配送条件变更与同批前后方案',['用户变更','影响预览','版本提交','重算与核定'],[
 N('a',0,0,'选择门店\n回收 / open / due / close','human'),N('b',1,0,'已有签收或作业事实？','decision'),N('c',2,0,'拒绝覆盖原输入\n转剩余任务处理','process','amber'),N('d',3,0,'记录变更前原条件\n与受影响原车次'),
 N('e',0,1,'conditions-preview\n原计划尚未改变'),N('f',1,1,'展示 before → after\n关联门店与车次'),N('g',2,1,'调度员已核对？','decision'),N('h',3,1,'conditions-apply\nreviewed=true'),
 N('i',0,2,'乐观锁 guard\nrevision 必须匹配'),N('j',1,2,'input_revision + 1\n清空原核定状态'),N('k',2,2,'原候选保留历史\n当前候选失效'),N('l',3,2,'新条件重新排车\n完整专项与整批核验'),
 N('m',0,3,'并发修改则 409\n刷新后再审阅','process','amber'),N('n',1,3,'parent_plan_id\ncondition_change_id','database'),N('o',2,3,'同批前后费用 / 车次\n公开收益与代价'),N('p',3,3,'重新人工核定\n不沿用旧确认','human')
],[E('a','b'),E('b','c','是','no'),E('b','f','否'),E('d','h'),E('e','f'),E('f','g'),E('g','h','是'),E('h','l'),E('i','j'),E('j','k'),E('k','l'),E('i','m','冲突','no'),E('l','n','','','bottom:top',[[1070,390],[450,390]]),E('n','o'),E('o','p')], 'decision_changes.preview / history · MultiWarehouseStore.save / calculate'),
G('manual','人工调序、调车与完整候选再验证',['编辑动作','集合守恒','重新计算','选择与确认'],[
 N('a',0,0,'人工调整同仓\n车次站序 / 门店归车','human'),N('b',1,0,'所有车辆均需提交\n闲置车填空列表'),N('c',2,0,'门店集合完全一致？\n无跨仓 / 漏单 / 重复','decision'),N('d',3,0,'不符合则拒绝提交\n原候选保持','process','amber'),
 N('e',0,1,'逐车基础 evaluator\n时窗 / 容量 / 回收'),N('f',1,1,'共享箱分组检查\n整组必须同车'),N('g',2,1,'manual 装载核验\n原箱规与操作约束'),N('h',3,1,'完整动作回放\n运费与工时重算'),
 N('i',0,2,'结果整批 audit\n没有放宽约束'),N('j',1,2,'通过全部检查？','decision'),N('k',2,2,'生成 manual_reorder\nparent_plan_id','database'),N('l',3,2,'不同候选并列比较\n保留前版'),
 N('m',0,3,'说明具体失败车次\n拒绝不可行候选','process','amber'),N('n',1,3,'当前 revision 匹配？','decision'),N('o',2,3,'调度员 reviewed\n明确采用当前方案','human'),N('p',3,3,'核定后可导出\n不等于企业派单','terminal')
],[E('a','b'),E('b','c'),E('c','d','否','no'),E('c','g','是'),E('e','f'),E('f','g'),E('g','h'),E('h','i','','','bottom:top',[[1070,245],[150,245]]),E('i','j'),E('j','k','是'),E('j','m','否','no'),E('k','l'),E('l','o'),E('n','o','是'),E('o','p')], 'MultiWarehouseStore.manual / adopt · warehouse_loading.operational_manual'),
G('spatial','核验坐标 → 3D 动态装卸的场景管线',['版本绑定','场景构造','渲染与动作','驾驶联动'],[
 N('a',0,0,'task / input_revision\nplan_id / warehouse / vehicle','database'),N('b',1,0,'当前候选匹配？','decision'),N('c',2,0,'拒绝过期 plan_id\n返回 Conflict','process','amber'),N('d',3,0,'仅重建选中车辆\n不重算整批搜索'),
 N('e',0,1,'箱体坐标与类别\nx,y,z / dx,dy,dz'),N('f',1,1,'状态帧 frames[]\n时间 / stop / boxes'),N('g',2,1,'动作 actions[]\nkind / box_ids / from,to'),N('h',3,1,'operational-scene.v1\n附任务版本与边界'),
 N('i',0,2,'Three.js 场景\n真实求解坐标 cm'),N('j',1,2,'装入 / 移出 / 卸下\n回收装入 / 放回动画'),N('k',2,2,'旋转 / 剖切 / 分解\n俯视 / 尾门 / 点选箱'),N('l',3,2,'统一播放时间轴\n装车 → 到店 → 回仓'),
 N('m',0,3,'journey-view\n路线 / 道路段 / 箱位'),N('n',1,3,'驾驶 / 跟车 / 路线全景\n到店切换装卸视图'),N('o',2,3,'只读方案推演\n播放不写签收数据'),N('p',3,3,'回仓状态仍保留\n共享箱与包装','terminal')
],[E('a','b'),E('b','c','否','no'),E('b','f','是'),E('d','h'),E('e','f'),E('f','g'),E('g','h'),E('h','l'),E('i','j'),E('j','k'),E('k','l'),E('m','n'),E('n','o'),E('o','p'),E('l','p')], 'multiwarehouse.spatial_view / journey_view · LogiSpatial3D · LogiJourneyTimeline'),
G('facts','执行事实、温度记录与剩余路线调整',['计划与核定','人工记录','剩余任务','事实保护'],[
 N('a',0,0,'核定当前输入候选\n创建执行任务','human'),N('b',1,0,'到店签收 / 实收包装\n时间 / 来源 / 数量'),N('c',2,0,'离散温度读数\n箱 ID / 环节 / 处置'),N('d',3,0,'追加事实记录\n不覆盖旧事实','database'),
 N('e',0,1,'实际回收 > 预计\n重量 / 容量风险'),N('f',1,1,'保留实际超量事实\n不可改回预测值'),N('g',2,1,'提取未服务门店\n剩余货物仍属原车'),N('h',3,1,'剩余顺序候选\n保留原车货物归属'),
 N('i',0,2,'锁定原输入与已签收\n原批次不能重算'),N('j',1,2,'核验剩余载荷与时间\n冷链与回仓再审'),N('k',2,2,'独立核验通过？','decision'),N('l',3,2,'人工采用剩余候选\n保留原执行事实','human'),
 N('m',0,3,'温度异常待处理\n不声称连续监测','process','amber'),N('n',1,3,'未通过 / 未知\n提交人工复核','process','amber'),N('o',2,3,'逐车返仓交接\n实际数量与时间'),N('p',3,3,'完成状态\n以真实记录为准','terminal')
],[E('a','b'),E('b','c'),E('c','d'),E('e','f'),E('f','g'),E('g','h'),E('d','h'),E('i','j'),E('j','k'),E('k','l','是'),E('k','n','否','no'),E('c','m','异常','no','top:top',[[750,25],[25,25],[25,410],[150,410]]),E('l','o'),E('o','p')], 'execution · remaining · MultiWarehouseStore.action · temperature records'),
G('learning','回收残差学习：时间隔离与验证激活',['回执数据','训练候选','时序留出验证','激活与监控'],[
 N('a',0,0,'真实 / 模拟数据分库域\nwarehouse + data_kind','database','purple'),N('b',1,0,'去重事件\nstore + decision_at'),N('c',2,0,'残差 r = actual−nominal\n纸箱 / 周转箱分量'),N('d',3,0,'EWMA 残差更新\n时间顺序训练'),
 N('e',0,1,'训练截止 trained_until\n冻结模型版本'),N('f',1,1,'验证窗口必须后置\n禁止未来信息泄露'),N('g',2,1,'留出 MAE / 改善\n风险上界覆盖'),N('h',3,1,'验证通过？','decision'),
 N('i',0,2,'未通过保留名义预测\n不激活候选','process','amber'),N('j',1,2,'模型 active + available_after\n仅生效时刻后可用','database','purple'),N('k',2,2,'expected / risk_upper\n预测与回执分层'),N('l',3,2,'未来回执漂移监控\n误差恶化 / 覆盖不足'),
 N('m',0,3,'数据不足 / 范围不匹配\n回到名义预测','process','amber'),N('n',1,3,'漂移触发回退 / 复核\n不改写历史预测'),N('o',2,3,'审计模型与事件\n模型版本可追溯','database','purple'),N('p',3,3,'研究模块可调用\n独立学习试验入口','terminal','purple')
],[E('a','b'),E('b','c'),E('c','d'),E('e','f'),E('f','g'),E('g','h'),E('d','h'),E('h','i','否','no','left:top',[[780,245],[150,245]]),E('h','l','是'),E('j','k'),E('k','l'),E('l','n'),E('m','n'),E('n','o'),E('o','p')], 'forecast_learning.ForecastStore.record / train / validate / activate / monitor','optional'),
G('adaptive','预测、重排、搬倒改良与固定车队护栏',['参考与新预测','候选搜索','严格护栏','交付状态'],[
 N('a',0,0,'名义方案完整复核\n形成 reference','database','purple'),N('b',1,0,'可用模型时间快照\n生成风险上界输入'),N('c',2,0,'原站序在新预测下复核\n必要时重新排车'),N('d',3,0,'有限布局 / 同站修复\n保留真实动作见证'),
 N('e',0,1,'候选完整可行？\n无新增迟到','decision'),N('f',1,1,'相同在用车辆集合\n固定在用车队'),N('g',2,1,'候选名义费用\n不得高于参考'),N('h',3,1,'逐车重量 / 体积\n装载率护栏 2 个百分点'),
 N('i',0,2,'模型需人工复核？\n覆盖不足 / 漂移','decision'),N('j',1,2,'全部护栏通过？','decision'),N('k',2,2,'REVIEWABLE_CANDIDATE\n仅可审阅候选','process','purple'),N('l',3,2,'独立 revalidate\n输入 / 策略 / 见证摘要'),
 N('m',0,3,'无安全候选\nNEEDS_MANUAL_REVIEW','process','amber'),N('n',1,3,'可行但未过护栏\nalternative 保留审阅'),N('o',2,3,'人工决策 / 回退\n无自动派单','human'),N('p',3,3,'默认排车流程外\n需显式运行试验','terminal','purple')
],[E('a','b'),E('b','c'),E('c','d'),E('d','h'),E('e','f','是'),E('f','g'),E('g','h'),E('h','j'),E('i','j','否'),E('j','k','是'),E('k','l'),E('j','n','否','no'),E('i','m','是','no'),E('m','n'),E('n','o'),E('l','p'),E('o','p')], 'adaptive_planning.plan_warehouse / _guard / revalidate / plan_batch','optional'),
G('ai','小航多模型聚合',['配置','校验','请求','输出'],[],[],'assistant_gateway'),
G('handoff','输入、方案、交接与企业接口版本链',['输入版本','方案版本','人工核定','导出与外部系统'],[
 N('a',0,0,'Excel SHA-256\n源行 / 映射版本','database'),N('b',1,0,'canonical input hash\ninput_revision','database'),N('c',2,0,'plan_id / parent_plan_id\n算法与核验结果','database'),N('d',3,0,'任务 revision\n乐观锁防覆盖'),
 N('e',0,1,'输入变化\n原核定自动失效','event'),N('f',1,1,'旧方案仍可追溯\n不覆盖历史'),N('g',2,1,'整批完整且核验通过？','decision'),N('h',3,1,'reviewed = true\n人工核定','human'),
 N('i',0,2,'JSON 方案包\n参数 / 来源 / 局限'),N('j',1,2,'CSV 作业清单\n逐车 / 逐站 / 回收'),N('k',2,2,'接口字段契约校验\n调度版本保持一致'),N('l',3,2,'dispatch_handoff\n作业交接数据包','process','amber'),
 N('m',0,3,'TMS 鉴权 / 字段映射\n实际环境接口设计','process','amber'),N('n',1,3,'GPS / 温度设备事件\n仍需业务接入','process','amber'),N('o',2,3,'企业回执与现场验收\n现场验收','process','amber'),N('p',3,3,'integration_contract\n接口字段契约','terminal','amber')
],[E('a','b'),E('b','c'),E('c','d'),E('e','f'),E('f','g'),E('g','h','是'),E('d','h'),E('i','j'),E('j','k'),E('k','l'),E('h','l'),E('m','n','','api'),E('n','o','','api'),E('o','p'),E('l','p')], 'integration_contract · MultiWarehouseStore.export_package / export_csv / adopt'),
G('exceptions','失败、未知、预算耗尽与回退决策树',['故障分类','保留证据','恢复动作','交付结果'],[
 N('a',0,0,'输入缺项 / 单位冲突\n未声明物理参数','event','amber'),N('b',1,0,'明确缺失字段\n不补造实测数据'),N('c',2,0,'人工补参数\n重新建立输入版本','human'),N('d',3,0,'待准备 / 待计算','terminal','amber'),
 N('e',0,1,'无共同准入车辆\n整组不允许上车','event','amber'),N('f',1,1,'NO_ELIGIBLE_VEHICLE\n保留全部未排门店'),N('g',2,1,'调整真实运力 / 准入\n不自动拆共享箱','human'),N('h',3,1,'修复后完整重算'),
 N('i',0,2,'几何 / 时间 / 冷链\n当前构造未通过','event','amber'),N('j',1,2,'失败码 + 车次 + 门店\n保留核验证据'),N('k',2,2,'可选布局 / 反向 / 补排\n仍用同一约束'),N('l',3,2,'已核验路线保留\n待排原因可见'),
 N('m',0,3,'预算耗尽 / 预测漂移\n无法证明后续安全','event','amber'),N('n',1,3,'SEARCH_BUDGET_EXHAUSTED\nNEEDS_MANUAL_REVIEW'),N('o',2,3,'回退已验证策略\n新输入仍需再核验','human'),N('p',3,3,'人工复核 / 不派单\n按结果标注核验状态','terminal','amber')
],[E('a','b'),E('b','c'),E('c','d'),E('e','f'),E('f','g'),E('g','h'),E('i','j'),E('j','k'),E('k','l'),E('m','n'),E('n','o'),E('o','p'),E('h','l'),E('l','p')], 'fleet_planning.unserved · operational error codes · adaptive status / rollback'),
G('scale','当前可运行规模与下一阶段扩展边界',['当前数据与能力','当前计算组织','可验证结果','后续扩展'],[
 N('a',0,0,'本批：1 中央仓\n116 原单 / 103 门店','database'),N('b',1,0,'本批：16 辆可用车\n当前候选 16 车次'),N('c',2,0,'所有 103 门店覆盖\n未安排 0 · 独立核验通过'),N('d',3,0,'可播放逐车箱位\n装车 / 到店 / 卸收 / 回仓'),
 N('e',0,1,'单仓 fleet 上限\n128 门店 / 24 车辆'),N('f',1,1,'普通多仓模式\n最多 16 仓独立求解'),N('g',2,1,'每仓 24 门店 / 8 车辆\n与 fleet 上限不可混淆'),N('h',3,1,'统一汇总与整批审计\n统一汇总 / 分仓核验'),
 N('i',0,2,'跨仓车辆与库存共享\n扩展设计','process','amber'),N('j',1,2,'多趟 / 充电 / 转货\n扩展设计','process','amber'),N('k',2,2,'实际道路与车型箱规\n现场测量待验证','process','amber'),N('l',3,2,'同批人工基线 / 费用\n实测收益待核对','process','amber'),
 N('m',0,3,'单仓影子运行\n录入真实履约回执','human'),N('n',1,3,'固定车队同口径对照\n逐站稳定性评估'),N('o',2,3,'接口联调与现场验证\n调度员确认派单','human'),N('p',3,3,'验证通过后扩围\n按验证结果扩展','terminal')
],[E('a','b'),E('b','c'),E('c','d'),E('e','f'),E('f','g'),E('g','h'),E('i','j','','api'),E('j','k','','api'),E('k','l'),E('m','n'),E('n','o'),E('o','p'),E('l','p')], 'multiwarehouse.warehouse_limits · retained central batch · integration_contract')
];

// Explicit dependency and branch edges for the stateful algorithm diagrams.
// Kept separate from the shape definitions so the topology is reviewable.
const revise=(id,edges)=>{window.technicalGraphs.find(g=>g.id===id).edges=edges;};
revise('orders',[E('a','b'),E('a','d','保留原表','','top:top'),E('b','c','否','no'),E('b','f','是'),E('c','d'),E('d','h'),E('e','f'),E('f','g'),E('g','h'),E('h','i'),E('i','j'),E('j','k'),E('k','l'),E('m','n'),E('n','o'),E('l','o'),E('o','p','通过'),E('o','m','缺项','feedback')]);
revise('search',[E('a','b'),E('b','c'),E('c','d'),E('d','e'),E('e','f'),E('f','g'),E('g','h'),E('h','j'),E('i','j','按目标排序'),E('j','k','否','no'),E('k','l'),E('j','n','是'),E('l','n'),E('m','n','预算'),E('n','o','是'),E('n','e','否','feedback','left:left',[[25,450],[25,210]]),E('o','p')]);
revise('packing',[E('a','b'),E('b','c'),E('c','d'),E('d','e'),E('e','f'),E('f','g'),E('g','h'),E('h','i'),E('i','j'),E('j','k'),E('k','l','是'),E('k','n','预算','no'),E('k','m','否','no'),E('l','o'),E('o','p','否'),E('o','e','是','feedback','left:left',[[25,450],[25,210]])]);
revise('cold',[E('a','b'),E('b','c'),E('c','d'),E('d','e','是'),E('d','m','缺项','no'),E('e','f'),E('f','g'),E('g','h'),E('h','i','通过'),E('h','m','失败','no'),E('i','j'),E('j','k'),E('k','l'),E('l','o'),E('n','g','移箱约束'),E('o','p','是'),E('o','f','否','feedback')]);
revise('unload',[E('a','b'),E('b','c','是'),E('b','m','否','no'),E('c','d'),E('d','e','是'),E('d','i','否'),E('e','f','允许'),E('e','m','禁止 / 不足','no'),E('f','g'),E('g','h'),E('h','i'),E('i','j'),E('j','k'),E('k','l'),E('l','n'),E('n','o'),E('o','p','是'),E('o','m','失败','no')]);
revise('recovery',[E('a','b'),E('b','c'),E('b','d'),E('c','f'),E('d','f'),E('e','f'),E('f','h'),E('g','h'),E('h','j'),E('i','k','工时'),E('j','k'),E('k','l'),E('l','m','否','no'),E('l','n','是'),E('n','o'),E('o','p')]);
revise('timeline',[E('a','b'),E('b','c'),E('c','d'),E('d','i'),E('e','i'),E('f','i'),E('g','i'),E('h','i'),E('i','j'),E('j','k'),E('k','l'),E('l','m'),E('m','n'),E('n','o'),E('o','p')]);
revise('learning',[E('a','b'),E('b','c'),E('c','d'),E('d','e'),E('e','f'),E('f','g'),E('g','h'),E('h','i','否','no'),E('h','j','是'),E('j','k'),E('k','l'),E('l','n','漂移'),E('m','i'),E('n','o'),E('o','p')]);
revise('changes',[E('a','b'),E('b','c','是','no'),E('b','d','否'),E('d','e'),E('e','f'),E('f','g'),E('g','h','是'),E('h','i'),E('i','j','版本匹配'),E('i','m','冲突','no'),E('j','k'),E('k','l'),E('l','n'),E('n','o'),E('o','p')]);
revise('spatial',[E('a','b'),E('b','c','否','no'),E('b','d','是'),E('d','e'),E('e','f'),E('f','g'),E('g','h'),E('h','i'),E('i','j'),E('j','k'),E('k','l'),E('h','m','同版本'),E('m','n'),E('n','o'),E('o','p'),E('l','p')]);
window.technicalGraphs.push(G('declared-rules','装车就绪、禁混装、退货隔离与声明能耗',['参数与业务声明','约束计算','冲突与处理','覆盖结果'],[
 N('a',0,0,'到库时刻 + 装车用时\npreparation 参数','database'),N('b',1,0,'ready≤min(发车,截止)？','decision'),N('c',2,0,'load_readiness\n准备不及则拒绝','process','amber'),N('d',3,0,'无参数：NOT_CONFIGURED\n不声称装车就绪已验证'),
 N('e',0,1,'货品 compatibility_group\n声明 incompatible_pairs','database'),N('f',1,1,'同车同时包含禁配对？','decision'),N('g',2,1,'incompatible_goods\n分车调整后重新核验','process','amber'),N('h',3,1,'禁止把几何能装下\n当作业务允许混装'),
 N('i',0,2,'要求车厢温区的冷箱\n独立 reserved cold_zone'),N('j',1,2,'冷箱全在温区内\n常温箱不侵占预留区'),N('k',2,2,'退货仅封闭常温隔离箱\n开放泄漏 / 冷链退货未建模','process','amber'),N('l',3,2,'临时区温区不足\n不得移出需温区冷箱'),
 N('m',0,3,'available / reserve / rate\n声明能量模型','database'),N('n',1,3,'d×rate+reserve≤available？','decision'),N('o',2,3,'energy 失败则拒绝\n未建模动态充电','process','amber'),N('p',3,3,'按 coverage 逐项公开\n已核验 / 未配置 / 未通过','terminal')
],[E('a','b'),E('b','c','否','no'),E('b','d','未声明','','top:top'),E('b','p','通过'),E('c','p','失败','no','right:right',[[1180,90],[1180,450]]),E('e','f'),E('f','g','是','no'),E('f','p','否'),E('g','h'),E('i','j'),E('j','l','移箱时'),E('k','l'),E('m','n'),E('n','o','否','no'),E('n','p','通过'),E('o','p'),E('d','p'),E('h','p'),E('l','p')], 'readiness_issue · incompatible_pairs · audit_state · returns · _coverage'));
const modelGraph=window.technicalGraphs.find(g=>g.id==='ai');
Object.assign(modelGraph,{title:'小航多模型聚合与业务上下文',status:'implemented',
 lanes:['配置与输入','会话与版本校验','模型调用','结果与操作'],
 nodes:[
 N('a',0,0,'提供方 / Base URL\n模型名称 / API Key','human'),N('b',1,0,'HTTPS 地址与字段校验\n服务端会话保管密钥'),N('c',2,0,'DeepSeek / Qwen\nGLM / Kimi'),N('d',3,0,'保存模型配置\nHttpOnly 会话 Cookie','database'),
 N('e',0,1,'用户问题\n所选批次与车辆','human'),N('f',1,1,'同源与 revision 校验\n绑定当前 plan_id'),N('g',2,1,'本机方案上下文\n数量 / 费用 / 冷链 / 回收'),N('h',3,1,'生成 messages[]\nsystem + user'),
 N('i',0,2,'POST chat/completions\nBearer API Key'),N('j',1,2,'HTTP / 超时 / 格式检查\n请求异常返回错误'),N('k',2,2,'读取模型回复\nchoices[0].message.content'),N('l',3,2,'answer / model / usage\n附批次版本与车辆'),
 N('m',0,3,'当前批次工具依据\ntask_revision / plan_id'),N('n',1,3,'助手消息展示\n与所选车辆关联'),N('o',2,3,'打开对应 3D 装卸\n查看当前车辆作业'),N('p',3,3,'调度员调整与核定\n业务动作逐次核验','human')],
 edges:[E('a','b'),E('b','c'),E('c','d'),E('d','h'),E('e','f'),E('f','g'),E('g','h'),E('h','i'),E('i','j'),E('j','k'),E('k','l'),E('l','n'),E('m','n'),E('n','o'),E('o','p')],
 source:'assistant_gateway.ModelGateway.save / complete · FinalHandler · /api/assistant/config / chat'});
