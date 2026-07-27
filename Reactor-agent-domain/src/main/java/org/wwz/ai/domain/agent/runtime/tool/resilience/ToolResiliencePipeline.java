package org.wwz.ai.domain.agent.runtime.tool.resilience;

import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.ratelimiter.RateLimiter;
import io.github.resilience4j.ratelimiter.RequestNotPermitted;
import io.github.resilience4j.retry.Retry;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import jakarta.annotation.Resource;
import java.util.function.Supplier;

/**
 * 工具调用韧性拦截链。
 * 执行顺序：RateLimiter → CircuitBreaker → Retry → actualCall。
 * <p>
 * 分级重试策略：
 * <ul>
 *     <li>所有工具：networkRetry（连接失败/连接超时/DNS失败）</li>
 *     <li>幂等工具额外：idempotentRetry（读超时 + 5xx 服务端错误）</li>
 *     <li>4xx 客户端错误、业务异常：不重试</li>
 * </ul>
 */
@Slf4j
@Component
public class ToolResiliencePipeline {

    @Resource
    private ToolResilienceConfig resilienceConfig;

    /**
     * 执行工具调用，经过完整的韧性拦截链。
     *
     * @param toolName   工具名称，用于隔离熔断器和限流器实例
     * @param idempotent 工具是否幂等，决定是否启用自动重试
     * @param actualCall 实际工具调用逻辑
     * @return 工具执行结果
     */
    public Object execute(String toolName, boolean idempotent, Supplier<Object> actualCall) {
        Supplier<Object> pipeline = actualCall;

        // 1. RateLimiter（所有工具都限流，防止打爆下游）
        pipeline = decorateWithRateLimiter(pipeline, toolName);

        // 2. CircuitBreaker（所有工具都熔断，按工具名隔离实例）
        pipeline = decorateWithCircuitBreaker(pipeline, toolName);

        // 3. 分级重试
        //    - 所有工具：networkRetry（连接失败/连接超时/DNS 失败，请求没到下游，100% 安全）
        //    - 幂等工具额外：idempotentRetry（读超时 + 5xx，重复执行无副作用）
        pipeline = decorateWithNetworkRetry(pipeline, toolName);
        if (idempotent) {
            pipeline = decorateWithIdempotentRetry(pipeline, toolName);
        }

        try {
            return pipeline.get();
        } catch (CallNotPermittedException e) {
            log.warn("工具 {} 熔断器已打开，拒绝调用: {}", toolName, e.getMessage());
            return buildDegradedResult(toolName, "工具暂时不可用（熔断保护中），请稍后重试");
        } catch (RequestNotPermitted e) {
            log.warn("工具 {} 限流拒绝: {}", toolName, e.getMessage());
            return buildDegradedResult(toolName, "工具调用频率过高，请稍后重试");
        } catch (Exception e) {
            log.error("工具 {} 韧性链执行异常: {}", toolName, e.getMessage(), e);
            throw e;
        }
    }

    private Supplier<Object> decorateWithRateLimiter(Supplier<Object> supplier, String toolName) {
        RateLimiter rateLimiter = resilienceConfig.getRateLimiterRegistry().rateLimiter(toolName);
        return RateLimiter.decorateSupplier(rateLimiter, supplier);
    }

    private Supplier<Object> decorateWithCircuitBreaker(Supplier<Object> supplier, String toolName) {
        CircuitBreaker circuitBreaker = resilienceConfig.getCircuitBreakerRegistry().circuitBreaker(toolName);
        return CircuitBreaker.decorateSupplier(circuitBreaker, supplier);
    }

    private Supplier<Object> decorateWithNetworkRetry(Supplier<Object> supplier, String toolName) {
        Retry retry = resilienceConfig.getNetworkRetryRegistry().retry(toolName);
        return Retry.decorateSupplier(retry, supplier);
    }

    private Supplier<Object> decorateWithIdempotentRetry(Supplier<Object> supplier, String toolName) {
        Retry retry = resilienceConfig.getIdempotentRetryRegistry().retry(toolName);
        return Retry.decorateSupplier(retry, supplier);
    }

    /**
     * 降级结果，当熔断或限流时返回友好提示。
     * 返回字符串而非 ToolResultPayload，因为此处需要兼容 BaseTool 和 MCP 工具两种返回类型。
     */
    private Object buildDegradedResult(String toolName, String message) {
        return message;
    }
}
