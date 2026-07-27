package org.wwz.ai.domain.agent.runtime;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.wwz.ai.domain.agent.runtime.enums.AgentType;
import org.wwz.ai.domain.agent.runtime.llm.LlmRouterService;

import java.util.Arrays;
import java.util.List;

/**
 * 智能体路由服务。
 * <p>
 * 根据用户 query 的内容特征（长度、关键词、句式）判断任务复杂度，
 * 自动选择最合适的 AgentType，替代前端通过 deepThink / outputStyle 硬编码策略。
 * <p>
 * 分类规则（可扩展）：
 * <ul>
 *   <li>简短问候、单句事实问答 → {@link AgentType#REACT}</li>
 *   <li>含分析/比较/调研/规划/报告等复杂语义 → {@link AgentType#PLAN_SOLVE}</li>
 *   <li>规则无法判断 → 调用 {@link LlmRouterService} 进行 LLM 分类</li>
 *   <li>默认兜底 → {@link AgentType#REACT}</li>
 * </ul>
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class RouterService {

    /** 复杂任务关键词（中文），命中任一即走 PLAN_SOLVE */
    private static final List<String> COMPLEX_KEYWORDS_CN = Arrays.asList(
            "分析", "比较", "对比", "调研", "研究", "规划", "计划",
            "报告", "总结", "评估", "预测", "梳理", "整理", "归纳",
            "推导", "论证", "综述", "方案", "设计", "优化", "改进",
            "为什么", "如何", "怎样", "哪些", "列举", "详细说明",
            "解决方案", "可行性", "建议", "推荐", "策略", "路线图"
    );

    /** 复杂任务关键词（英文），命中任一即走 PLAN_SOLVE */
    private static final List<String> COMPLEX_KEYWORDS_EN = Arrays.asList(
            "analyze", "analyse", "compare", "contrast", "research",
            "investigate", "plan", "planning", "report", "summary",
            "summarize", "evaluate", "evaluation", "predict", "forecast",
            "why", "how to", "what are the", "differences", "comparison",
            "solution", "proposal", "strategy", "roadmap", "architecture",
            "design", "optimize", "improve", "refactor", "analysis",
            "explain in detail", "comprehensive", "thorough", "deep dive"
    );

    /** 极简问候/单句，命中即走 REACT（通常只有几个字） */
    private static final List<String> SIMPLE_GREETINGS = Arrays.asList(
            "你好", "您好", "嗨", "hi", "hello", "hey", "早上好", "下午好", "晚上好"
    );

    /** 短查询阈值（不含空格字符数），低于此值默认走 REACT */
    private static final int SHORT_QUERY_THRESHOLD = 15;

    private final LlmRouterService llmRouterService;

    /**
     * 分析 query 并返回推荐的 AgentType。
     * 先走快速规则，规则未命中时委托 LLM 分类。
     *
     * @param query           用户输入的自然语言问题
     * @param historyDialogue 会话历史摘要（可为空）
     * @return 推荐使用的 AgentType
     */
    public AgentType decide(String query, String historyDialogue) {
        if (query == null || query.trim().isEmpty()) {
            return AgentType.REACT;
        }

        String trimmed = query.trim();
        String lower = trimmed.toLowerCase();

        // 1. 极简问候 → REACT
        for (String greeting : SIMPLE_GREETINGS) {
            if (lower.startsWith(greeting) || lower.equals(greeting)) {
                log.debug("[RouterService] greeting matched, query='{}' → REACT", trimmed);
                return AgentType.REACT;
            }
        }

        // 2. 短查询 → REACT（大概率是简单事实问答）
        if (trimmed.replaceAll("\\s+", "").length() < SHORT_QUERY_THRESHOLD) {
            log.debug("[RouterService] short query ({} chars), query='{}' → REACT",
                    trimmed.replaceAll("\\s+", "").length(), trimmed);
            return AgentType.REACT;
        }

        // 3. 复杂关键词命中 → PLAN_SOLVE
        if (matchComplexKeyword(lower)) {
            log.debug("[RouterService] keyword matched, query='{}' → PLAN_SOLVE", trimmed);
            return AgentType.PLAN_SOLVE;
        }

        // 4. LLM 兜底分类
        log.info("[RouterService] no rule matched, falling back to LLM classifier, query='{}'", trimmed);
        return llmRouterService.classify(trimmed, historyDialogue);
    }

    private boolean matchComplexKeyword(String lower) {
        for (String keyword : COMPLEX_KEYWORDS_CN) {
            if (lower.contains(keyword)) return true;
        }
        for (String keyword : COMPLEX_KEYWORDS_EN) {
            if (lower.contains(keyword)) return true;
        }
        return false;
    }
}
