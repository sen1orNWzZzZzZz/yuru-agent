"""
Agent V3 抽象基类
所有子Agent继承此类，实现统一的execute接口
"""
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from app.integrations.llm_client import LLMClient

logger = logging.getLogger(__name__)


class AgentResult:
    """Agent执行结果统一封装"""

    def __init__(self, agent_type: str, agent_name: str, status: str = "completed",
                 data: dict = None, reasoning: str = "", duration_ms: int = 0,
                 error: str = "", usage: dict | None = None):
        self.agent_type = agent_type
        self.agent_name = agent_name
        self.status = status
        self.data = data or {}
        self.reasoning = reasoning
        self.duration_ms = duration_ms
        self.error = error
        self.usage = usage or {}

    def to_dict(self) -> dict:
        return {
            "agent_type": self.agent_type,
            "agent_name": self.agent_name,
            "status": self.status,
            "data": self.data,
            "reasoning": self.reasoning,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "usage": self.usage,
        }


class BaseAgentV3(ABC):
    """
    V3 Agent抽象基类
    每个子Agent只需实现：
    - _build_prompt(): 构建LLM提示词
    - _execute_with_db(): 数据库查询逻辑
    """

    agent_type: str = ""
    agent_name: str = ""
    # 子类可覆盖，要求 LLM 按 JSON Schema 输出
    response_schema: dict | None = None

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm = llm_client
        self.use_llm = llm_client is not None and llm_client.is_available()
        # 子 Agent 可覆盖：天气类建议短 TTL，行程生成类建议长 TTL
        self.llm_cache_ttl = 86400

    def execute(self, context: dict[str, Any]) -> AgentResult:
        """
        Agent统一入口
        1. 从数据库获取数据
        2. 如有LLM，让LLM分析并给出建议
        3. 封装结果返回
        """
        start = time.time()
        try:
            # Step 1: 从数据库/外部API获取数据
            db_data = self._execute_with_db(context)

            # Step 2: 如有LLM，增强分析
            reasoning = ""
            usage = {}
            if self.use_llm:
                llm_result = self._call_llm(context, db_data)
                if llm_result.get("success"):
                    reasoning = llm_result.get("reasoning", "")
                    usage = llm_result.get("usage", {})
                    # LLM可能给出筛选/排序建议
                    db_data = self._merge_llm_result(db_data, llm_result)

            duration_ms = int((time.time() - start) * 1000)
            return AgentResult(
                agent_type=self.agent_type,
                agent_name=self.agent_name,
                status="completed",
                data=db_data,
                reasoning=reasoning or self._default_reasoning(context, db_data),
                duration_ms=duration_ms,
                usage=usage,
            )

        except Exception as e:
            logger.error(f"[{self.agent_name}] 执行失败: {e}")
            return AgentResult(
                agent_type=self.agent_type,
                agent_name=self.agent_name,
                status="failed",
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    def _call_llm(self, context: dict, db_data: dict) -> dict:
        """调用LLM进行智能分析"""
        if not self.use_llm:
            return {"success": False}

        try:
            system_prompt, user_prompt = self._build_prompt(context, db_data)
            if self.response_schema:
                result = self.llm.chat_structured(
                    system_prompt, user_prompt,
                    response_schema=self.response_schema,
                    temperature=0.5, max_tokens=2000,
                    use_cache=True, cache_ttl=self.llm_cache_ttl,
                )
            else:
                result = self.llm.chat(
                    system_prompt, user_prompt,
                    temperature=0.5, max_tokens=2000,
                    use_cache=True, cache_ttl=self.llm_cache_ttl,
                )

            if result["success"]:
                usage = result.get("usage", {})
                if "data" in result:
                    return {"success": True, "usage": usage, **result["data"]}
                parsed = self.llm.extract_json(result["content"])
                if parsed:
                    return {"success": True, "usage": usage, **parsed}
                # 非JSON返回，作为reasoning
                return {"success": True, "usage": usage, "reasoning": result["content"][:500]}
            return {"success": False, "error": result.get("error", "")}
        except Exception as e:
            logger.warning(f"[{self.agent_name}] LLM调用失败: {e}")
            return {"success": False, "error": str(e)}

    @abstractmethod
    def _execute_with_db(self, context: dict[str, Any]) -> dict[str, Any]:
        """子类实现：从数据库/外部API获取原始数据"""
        pass

    @abstractmethod
    def _build_prompt(self, context: dict, db_data: dict) -> tuple:
        """
        子类实现：构建LLM提示词
        Returns: (system_prompt, user_prompt)
        """
        pass

    def _merge_llm_result(self, db_data: dict, llm_result: dict) -> dict:
        """
        合并LLM分析结果到数据库数据
        子类可覆盖以自定义合并逻辑
        """
        if "recommendations" in llm_result:
            db_data["llm_recommendations"] = llm_result["recommendations"]
        if "analysis" in llm_result:
            db_data["llm_analysis"] = llm_result["analysis"]
        return db_data

    def _default_reasoning(self, context: dict, db_data: dict) -> str:
        """默认推理说明"""
        return f"{self.agent_name}完成数据检索"
