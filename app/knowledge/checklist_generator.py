"""旅行装备与 Tips Checklist 生成器."""

import logging
import re

from app.knowledge.retriever import TipsRetriever

logger = logging.getLogger(__name__)


class ChecklistGenerator:
    """基于 RAG 生成个性化旅行 Checklist."""

    def __init__(self, retriever: TipsRetriever | None = None):
        self.retriever = retriever or TipsRetriever()

    def generate(
        self,
        destination: str,
        days: int,
        travelers: int = 2,
        season: str | None = None,
        special_needs: str | None = None,
        style: str | None = None,
    ) -> dict:
        """
        生成旅行 Checklist.

        Args:
            destination: 目的地
            days: 天数
            travelers: 人数
            season: 季节/月份，如 "夏季"、"6月"
            special_needs: 特殊需求，如 "带老人"、"亲子"、"高原"
            style: 旅行风格，如 "摄影"、"美食"、"户外"
        """
        query = self._build_query(destination, season, special_needs, style)
        results = self.retriever.retrieve(query, n_results=5)

        categories = {}
        sources = set()

        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]

        for doc, meta in zip(documents, metadatas):
            if not doc:
                continue
            source = meta.get("source", "未知")
            sources.add(source)

            # 从文档中提取 checklist 项
            items = self._extract_checklist_items(doc)
            if not items:
                continue

            category = self._detect_category(meta, doc)
            categories.setdefault(category, []).extend(items)

        # 去重并保持顺序
        for category in categories:
            seen = set()
            unique = []
            for item in categories[category]:
                key = item.strip()
                if key and key not in seen:
                    seen.add(key)
                    unique.append(item)
            categories[category] = unique

        # 添加个性化补充项
        personalized = self._personalized_items(
            destination, days, travelers, season, special_needs, style
        )
        if personalized:
            categories.setdefault("个性化建议", []).extend(personalized)

        return {
            "destination": destination,
            "days": days,
            "travelers": travelers,
            "season": season,
            "special_needs": special_needs,
            "style": style,
            "categories": categories,
            "sources": sorted(sources),
        }

    def _build_query(
        self,
        destination: str,
        season: str | None,
        special_needs: str | None,
        style: str | None,
    ) -> str:
        parts = [f"{destination}旅行"]
        if season:
            parts.append(f"{season}出行")
        if special_needs:
            parts.append(special_needs)
        if style:
            parts.append(f"{style}旅行")
        return " ".join(parts)

    def _extract_checklist_items(self, doc: str) -> list[str]:
        """从 Markdown 文档中提取 `- [ ] 内容` 形式的 checklist 项."""
        items = re.findall(r"- \[[ xX]\] (.+)", doc)
        return [item.strip() for item in items if item.strip()]

    def _detect_category(self, metadata: dict, doc: str) -> str:
        """根据元数据或内容标题判断分类."""
        tags = metadata.get("tags", "")
        if isinstance(tags, str):
            tags = tags.split(",")

        category_map = {
            "装备": "装备清单",
            "防晒": "防护装备",
            "保暖": "衣物装备",
            "雨具": "雨天装备",
            "安全": "安全事项",
            "亲子": "亲子出行",
            "老人": "带老人出行",
            "摄影": "摄影装备",
            "宠物": "宠物出行",
        }

        for tag in tags:
            tag = tag.strip()
            if tag in category_map:
                return category_map[tag]

        # 根据标题判断
        if doc.startswith("#"):
            title_match = re.match(r"#+\s*(.+)", doc)
            if title_match:
                return title_match.group(1).strip()

        return "通用建议"

    def _personalized_items(
        self,
        destination: str,
        days: int,
        travelers: int,
        season: str | None,
        special_needs: str | None,
        style: str | None,
    ) -> list[str]:
        """根据用户输入生成个性化补充项."""
        items = []
        needs = (special_needs or "") + " " + (style or "")
        needs = needs.lower()

        if "老人" in needs or "长辈" in needs:
            items.append("为老人准备常用药品和血压计/血糖仪")
            items.append("预订无障碍设施较好的酒店")

        if "亲子" in needs or "儿童" in needs or "小孩" in needs:
            items.append("准备儿童推车、零食、安抚玩具")
            items.append("随身携带儿童身份证明复印件")

        if "高原" in needs or "西藏" in needs or "丽江" in destination or "大理" in destination:
            items.append("准备防晒霜、墨镜、保暖外套")
            items.append("携带葡萄糖口服液和便携氧气瓶")

        if "海边" in needs or "沙滩" in needs or "厦门" in destination:
            items.append("准备泳衣、防水手机袋、沙滩拖鞋")

        if "摄影" in needs:
            items.append("准备三脚架、备用电池、存储卡")

        if "宠物" in needs:
            items.append("准备宠物疫苗证明和便携食盆")

        if days > 5:
            items.append("准备足够的换洗衣物和便携洗衣液")

        if travelers > 4:
            items.append("提前预订大桌餐厅和多人交通")

        if season and ("冬" in season or "12月" in season or "1月" in season or "2月" in season):
            items.append("准备保暖衣物、暖宝宝、保湿霜")

        if season and ("雨" in season or "6月" in season or "7月" in season or "8月" in season):
            items.append("准备雨伞、雨衣、防水鞋套")

        return items
