"""Local intent routing for explicit dispatcher requests. All writes are proposals."""

import re


class LocalDispatchPlanner:
    is_local = True

    def plan(self, *, context, message, tools):
        self.actions = []
        self.disposition = 'supported'
        self.last_answer = ''
        request = message.strip().rstrip('。！？!?').strip()
        aliases = context['display_aliases']
        selected = context['selected']

        if request in ('核对订单', '核对这批订单', '检查这批订单'):
            return ['inspect']
        if request in ('计算配送方案', '帮我排车', '生成排车方案'):
            if context['summary']['has_execution_facts']:
                return self.clarify('这批已有作业记录。可以查看当前车次，或在回收返仓区处理变化。')
            return ['dispatch']
        if request in ('比较方案', '比较两套方案', '解释方案', '查看运费', '这批的运费怎么算'):
            return ['compare']
        if request in ('检查风险', '查看待处理', '哪里需要复核'):
            return ['risks']
        if request in ('检查约束', '检查冷链', '看看哪些规则已核对'):
            return ['coverage_audit']
        if request in ('查看本车装卸', '查看本车回收空间'):
            return ['spatial_inspect']
        if request in ('核对本车', '查看本车运费', '这辆车的运费怎么算', '这辆车为什么这么排'):
            return ['route_review']

        move = re.fullmatch(r'(?:把|将)\s*(门店\s*\d+)\s*(?:调到|移到)\s*(车辆\s*\d+)', request)
        if move:
            order_id = self.resolve(move[1], aliases['orders'])
            vehicle_id = self.resolve(move[2], aliases['vehicles'])
            if not order_id or not vehicle_id:
                return self.clarify('请核对方案里的门店和车辆编号。')
            self.actions = [dict(kind='move_order', order_id=order_id, vehicle_id=vehicle_id)]
            return ['adjust']

        pickup = re.fullmatch(r'(?:把|将)\s*(门店\s*\d+|当前门店|这家店)\s*的?(?:预计)?回收\s*(?:改为|设为)\s*(\d{1,6})\s*(?:个|只)?\s*(纸箱|周转箱)', request)
        if pickup:
            order_id = (selected.get('order_id') if pickup[1] in ('当前门店', '这家店')
                        else self.resolve(pickup[1], aliases['orders']))
            if not order_id:
                return self.clarify('请先在路线中选中门店，或在请求里写明门店编号。')
            field = 'cartons' if pickup[3] == '纸箱' else 'totes'
            self.actions = [dict(kind='pickup', order_id=order_id, field=field, value=int(pickup[2]))]
            return ['rules']

        if request in ('重算剩余路线', '重排剩余路线'):
            if not context['confirmed']:
                return self.clarify('请先确认方案，再按作业记录核对剩余路线。')
            if context['summary']['operational_policy_warehouses']:
                return self.clarify('请先打开车次核对在车箱位和冷链时限，这类任务的在途调整由调度员处理。')
            return ['remaining']
        return self.clarify('可以问「比较两套方案」「这辆车的运费怎么算」，或说「把门店1的预计回收改为3个周转箱」。自由提问可切换到已配置的模型。')

    @staticmethod
    def resolve(label, aliases):
        clean = ''.join(label.split())
        return next((item['id'] for item in aliases if item['label'] == clean), None)

    def clarify(self, answer):
        self.disposition = 'unsupported'
        self.last_answer = answer
        return []
