package org.wwz.ai.domain.agent.runtime.llm;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.springframework.ai.chat.messages.Message;
import org.springframework.ai.chat.messages.SystemMessage;
import org.springframework.ai.chat.messages.UserMessage;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.ai.chat.prompt.Prompt;
import org.springframework.ai.openai.OpenAiChatModel;
import org.springframework.ai.openai.OpenAiChatOptions;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Service;
import org.wwz.ai.domain.agent.reactor.config.ReactorConfig;
import org.wwz.ai.domain.agent.runtime.enums.AgentType;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 基于 LLM 的 AgentType 分类器。
 * 当 {@link org.wwz.ai.domain.agent.runtime.RouterService} 的快速规则无法判断时，
 * 通过一次轻量模型调用完成意图分类，最终兜底 REACT。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class LlmRouterService {

    private static final String DEFAULT_ROUTER_SYSTEM_PROMPT = """
            你是一个任务路由器。根据用户的问题和对话历史，判断应该使用哪种执行策略。

            ## 可选策略
            - react: 单步或少步即可完成的任务。简单问答、事实查询、直接工具调用、简短闲聊、单文件操作等。
            - plan_solve: 需要多步骤规划、分解子任务、中间验证的复杂任务。分析报告、方案设计、多步调研、对比分析等。

            ## 判断原则
            1. 如果任务可以通过 1-2 次工具调用或直接回答完成 → react
            2. 如果任务需要分解为多个子任务并依次执行 → plan_solve
            3. 如果不确定，优先选 react（轻量策略优先）

            ## 输出格式
            严格返回 JSON，不要包含任何其他内容：
            ```json
            {"strategy": "react 或 plan_solve", "confidence": 0.0到1.0, "reason": "一句话说明判断依据"}
            ```
            """;

    private static final Pattern JSON_CODE_BLOCK_PATTERN = Pattern.compile("```json\\s*([\\s\\S]*?)\\s*```");
    private static final Pattern JSON_OBJECT_PATTERN = Pattern.compile("\\{[\\s\\S]*?\\}");

    private final ReactorConfig reactorConfig;
    private final LlmChatModelResolver chatModelResolver;
    private final Environment environment;

    /**
     * 当关键字规则无法判断时，调用 LLM 进行意图分类。
     *
     * @param query           当前用户 query
     * @param historyDialogue 会话历史摘要（可为空）
     * @return 推荐的 AgentType，异常或低置信度时兜底 REACT
     */
    public AgentType classify(String query, String historyDialogue) {
        long startTime = System.currentTimeMillis();
        String requestId = "router-" + Thread.currentThread().getId();

        try {
            String systemPrompt = StringUtils.defaultIfBlank(reactorConfig.getRouterSystemPrompt(), DEFAULT_ROUTER_SYSTEM_PROMPT);
            String userPrompt = buildUserPrompt(query, historyDialogue);

            List<Message> messages = List.of(
                    new SystemMessage(systemPrompt),
                    new UserMessage(userPrompt)
            );

            String modelName = reactorConfig.getRouterModelName();
            LLMSettings settings = resolveLlmSettings(modelName);
            OpenAiChatModel chatModel = chatModelResolver.resolve(settings);

            OpenAiChatOptions options = OpenAiChatOptions.builder()
                    .model(settings.getModel())
                    .temperature(0.1)
                    .maxTokens(200)
                    .build();

            Prompt prompt = new Prompt(messages, options);

            log.info("[LlmRouterService] classify start, requestId={}, model={}", requestId, modelName);
            String content = callWithTimeout(chatModel, prompt, reactorConfig.getRouterTimeoutSeconds());
            log.info("[LlmRouterService] classify finish, requestId={}, cost={}ms, raw={}",
                    requestId, System.currentTimeMillis() - startTime, content);

            return parseRouterResponse(content);
        } catch (Exception e) {
            log.warn("[LlmRouterService] classify failed, requestId={}, fallback to REACT", requestId, e);
            return AgentType.REACT;
        }
    }

    private String callWithTimeout(OpenAiChatModel chatModel, Prompt prompt, int timeoutSeconds) throws Exception {
        int effectiveTimeout = timeoutSeconds > 0 ? timeoutSeconds : 10;
        return java.util.concurrent.CompletableFuture.supplyAsync(() -> {
            ChatResponse response = chatModel.call(prompt);
            return response.getResult().getOutput().getText();
        }).get(effectiveTimeout, TimeUnit.SECONDS);
    }

    private String buildUserPrompt(String query, String historyDialogue) {
        StringBuilder sb = new StringBuilder();
        if (StringUtils.isNotBlank(historyDialogue)) {
            sb.append("## 对话历史\n").append(historyDialogue).append("\n\n");
        }
        sb.append("## 用户问题\n").append(query).append("\n\n");
        sb.append("请判断这个任务应该使用哪种执行策略，严格返回要求的 JSON。");
        return sb.toString();
    }

    private AgentType parseRouterResponse(String content) {
        if (StringUtils.isBlank(content)) {
            log.warn("[LlmRouterService] empty response, fallback to REACT");
            return AgentType.REACT;
        }

        String json = extractJson(content);
        if (StringUtils.isBlank(json)) {
            log.warn("[LlmRouterService] no json found in content='{}', fallback to REACT", content);
            return AgentType.REACT;
        }

        try {
            JSONObject obj = JSON.parseObject(json);
            String strategy = obj.getString("strategy");
            Double confidence = obj.getDouble("confidence");

            double threshold = reactorConfig.getRouterConfidenceThreshold() != null
                    ? reactorConfig.getRouterConfidenceThreshold()
                    : 0.6;

            if (confidence != null && confidence < threshold) {
                log.info("[LlmRouterService] low confidence ({} < {}), fallback to REACT", confidence, threshold);
                return AgentType.REACT;
            }

            if ("plan_solve".equalsIgnoreCase(strategy)) {
                log.info("[LlmRouterService] classified as PLAN_SOLVE (confidence={})", confidence);
                return AgentType.PLAN_SOLVE;
            }

            log.info("[LlmRouterService] classified as REACT (strategy={}, confidence={})", strategy, confidence);
            return AgentType.REACT;
        } catch (Exception e) {
            log.warn("[LlmRouterService] parse failed, content='{}', fallback to REACT", content, e);
            return AgentType.REACT;
        }
    }

    private String extractJson(String content) {
        Matcher codeBlockMatcher = JSON_CODE_BLOCK_PATTERN.matcher(content);
        if (codeBlockMatcher.find()) {
            return codeBlockMatcher.group(1).trim();
        }
        Matcher objectMatcher = JSON_OBJECT_PATTERN.matcher(content);
        if (objectMatcher.find()) {
            return objectMatcher.group().trim();
        }
        return content.trim();
    }

    /**
     * 解析模型配置。优先读取 ReactorConfig.llmSettingsMap，其次回退到 llm.default.*。
     * 与 ReactorRuntimeDependencies.resolveLlmSettings 逻辑保持一致。
     */
    private LLMSettings resolveLlmSettings(String modelName) {
        String normalizedModelName = modelName == null ? "" : modelName.trim();

        Map<String, LLMSettings> settingsMap = reactorConfig.getLlmSettingsMap();
        if (settingsMap != null && !normalizedModelName.isBlank()) {
            LLMSettings settings = settingsMap.get(normalizedModelName);
            if (settings != null) {
                return settings;
            }
        }

        return buildDefaultLlmSettings(normalizedModelName);
    }

    private LLMSettings buildDefaultLlmSettings(String modelName) {
        return LLMSettings.builder()
                .model(StringUtils.isNotBlank(modelName) ? modelName : environment.getProperty("llm.default.model", "gpt-4o-0806"))
                .maxTokens(parseInt(environment.getProperty("llm.default.max_tokens"), 16384))
                .temperature(parseDouble(environment.getProperty("llm.default.temperature"), 0.0))
                .baseUrl(environment.getProperty("llm.default.base_url", ""))
                .interfaceUrl(environment.getProperty("llm.default.interface_url", "/v1/chat/completions"))
                .functionCallType(environment.getProperty("llm.default.function_call_type", "function_call"))
                .apiKey(environment.getProperty("llm.default.apikey", ""))
                .maxInputTokens(parseInt(environment.getProperty("llm.default.max_input_tokens"), 100000))
                .extParams(new HashMap<>())
                .build();
    }

    private int parseInt(String value, int defaultValue) {
        if (value == null || value.isBlank()) {
            return defaultValue;
        }
        try {
            return Integer.parseInt(value.trim());
        } catch (NumberFormatException ignore) {
            return defaultValue;
        }
    }

    private double parseDouble(String value, double defaultValue) {
        if (value == null || value.isBlank()) {
            return defaultValue;
        }
        try {
            return Double.parseDouble(value.trim());
        } catch (NumberFormatException ignore) {
            return defaultValue;
        }
    }
}
