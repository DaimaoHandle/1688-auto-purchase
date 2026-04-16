"""
选择器健康检测模块。

1688 使用 CSS Modules，改版后选择器可能失效。
本模块追踪每个选择器的命中/失败情况，区分临时失败和改版失效，生成诊断报告。
"""
import os
import json
import logging
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger("1688-auto")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SelectorTracker:
    """追踪选择器的命中/失败统计。"""

    def __init__(self):
        # {group_name: {selector: {"hit": N, "miss": N, "consecutive_miss": N}}}
        self._stats = defaultdict(lambda: defaultdict(lambda: {"hit": 0, "miss": 0, "consecutive_miss": 0}))
        # {group_name: consecutive_group_miss}
        self._group_consecutive_miss = defaultdict(int)
        # 被标记为疑似失效的选择器组
        self._suspected_broken = set()
        # 失效阈值：同一组连续失败 N 次视为疑似改版
        self.broken_threshold = 3

    def record_hit(self, group_name: str, selector: str):
        """记录选择器命中。"""
        stat = self._stats[group_name][selector]
        stat["hit"] += 1
        stat["consecutive_miss"] = 0
        self._group_consecutive_miss[group_name] = 0
        # 曾被标记为失效但现在成功了，移除标记
        self._suspected_broken.discard(group_name)

    def record_group_miss(self, group_name: str):
        """记录某个选择器组整体未命中（所有选择器都没找到）。"""
        self._group_consecutive_miss[group_name] += 1
        # 更新组内所有选择器的连续失败计数
        for selector in self._stats[group_name]:
            self._stats[group_name][selector]["miss"] += 1
            self._stats[group_name][selector]["consecutive_miss"] += 1

        if self._group_consecutive_miss[group_name] >= self.broken_threshold:
            if group_name not in self._suspected_broken:
                self._suspected_broken.add(group_name)
                logger.warning(f"[SELECTOR_BROKEN] 选择器组 '{group_name}' 连续 "
                             f"{self._group_consecutive_miss[group_name]} 次未命中，疑似 1688 改版失效")

    def is_suspected_broken(self, group_name: str) -> bool:
        """检查某个选择器组是否被标记为疑似失效。"""
        return group_name in self._suspected_broken

    def get_summary(self) -> dict:
        """获取所有选择器组的健康摘要。"""
        summary = {}
        for group_name in self._stats:
            selectors = self._stats[group_name]
            total_hit = sum(s["hit"] for s in selectors.values())
            total_miss = sum(s["miss"] for s in selectors.values())
            summary[group_name] = {
                "total_hit": total_hit,
                "total_miss": total_miss,
                "consecutive_miss": self._group_consecutive_miss[group_name],
                "suspected_broken": group_name in self._suspected_broken,
                "selector_count": len(selectors),
            }
        return summary

    def get_broken_groups(self) -> list:
        """获取所有疑似失效的选择器组名称。"""
        return list(self._suspected_broken)

    def save_report(self):
        """保存选择器健康报告到 reports/ 目录。"""
        report_dir = os.path.join(_ROOT, "reports")
        os.makedirs(report_dir, exist_ok=True)

        now = datetime.now()
        report = {
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "summary": self.get_summary(),
            "broken_groups": self.get_broken_groups(),
            "details": {
                group: {sel: stats for sel, stats in selectors.items()}
                for group, selectors in self._stats.items()
            },
        }

        filename = now.strftime("selector_health_%Y%m%d_%H%M%S.json")
        filepath = os.path.join(report_dir, filename)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info(f"选择器健康报告: {filepath}")
        except Exception as e:
            logger.warning(f"保存选择器报告失败: {e}")

        # 打印摘要
        broken = self.get_broken_groups()
        if broken:
            print(f"\n  [!] 以下选择器疑似因 1688 改版失效:")
            for g in broken:
                s = self.get_summary()[g]
                print(f"      {g}: 连续失败 {s['consecutive_miss']} 次")
            print(f"  请检查报告: {filepath}\n")

        return filepath


# 全局单例
_tracker = SelectorTracker()


def get_tracker() -> SelectorTracker:
    """获取全局选择器追踪器。"""
    return _tracker


def try_selectors(page, selectors: list, group_name: str, query_all: bool = False, check_visible: bool = False):
    """
    统一的选择器遍历函数。替代各模块中散落的 for sel in SELECTORS 循环。

    Args:
        page: Playwright page 对象
        selectors: 选择器列表
        group_name: 选择器组名（用于追踪统计）
        query_all: True 用 query_selector_all，False 用 query_selector
        check_visible: True 额外检查元素是否可见

    Returns:
        query_all=False: 匹配的元素或 None
        query_all=True: 匹配的元素列表（可能为空）
    """
    tracker = get_tracker()

    for sel in selectors:
        try:
            if query_all:
                elements = page.query_selector_all(sel)
                if elements:
                    if check_visible:
                        visible = [el for el in elements if el.is_visible()]
                        if visible:
                            tracker.record_hit(group_name, sel)
                            return visible
                    else:
                        tracker.record_hit(group_name, sel)
                        return elements
            else:
                el = page.query_selector(sel)
                if el:
                    if check_visible and not el.is_visible():
                        continue
                    tracker.record_hit(group_name, sel)
                    return el
        except Exception:
            continue

    # 所有选择器都未命中
    tracker.record_group_miss(group_name)
    if query_all:
        return []
    return None
