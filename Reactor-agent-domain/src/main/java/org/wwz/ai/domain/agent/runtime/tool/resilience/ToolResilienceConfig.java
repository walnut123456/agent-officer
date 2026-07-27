package org.wwz.ai.domain.agent.runtime.tool.resilience;

import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.github.resilience4j.ratelimiter.RateLimiterConfig;
import io.github.resilience4j.ratelimiter.RateLimiterRegistry;
import io.github.resilience4j.core.IntervalFunction;
import io.github.resilience4j.retry.RetryConfig;
import io.github.resilience4j.retry.RetryRegistry;
import io.github.resilience4j.timelimiter.TimeLimiterConfig;
import io.github.resilience4j.timelimiter.TimeLimiterRegistry;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.Resource;
import lombok.Getter;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.wwz.ai.domain.agent.reactor.config.ReactorConfig;

import java.io.IOException;
import java.net.ConnectException;
import java.net.SocketTimeoutException;
import java.time.Duration;
import java.util.concurrent.TimeoutException;

/**
 * 工具调用韧性框架配置。
 * 基于 Resilience4j 构建 Retry / CircuitBreaker / RateLimiter / TimeLimiter 注册表，
 * 配置值从 ReactorConfig（application-dev.yml）读取。
 *
 * 重试策略分级：
 * - networkRetryRegistry：所有工具都用，只重试连接级错误（连接失败/连接超时/DNS失败）
 * - idempotentRetryRegistry：仅幂等工具使用，额外重试读超时和 5xx 服务端错误
 */
@Slf4j
@Getter
@Component
public class ToolResilienceConfig {

    @Resource
    private ReactorConfig reactorConfig;

    /** 网络级重试（所有工具）：只重试连接级错误 */
    private RetryRegistry networkRetryRegistry;

    /** 幂等重试（仅幂等工具）：网络错误 + 读超时 + 5xx */
    private RetryRegistry idempotentRetryRegistry;

    private CircuitBreakerRegistry circuitBreakerRegistry;
    private RateLimiterRegistry rateLimiterRegistry;
    private TimeLimiterRegistry timeLimiterRegistry;

    @PostConstruct
    public void init() {
        ReactorConfig.ResilienceProps props = reactorConfig.getResilience();

        this.networkRetryRegistry = buildNetworkRetryRegistry(props);
        this.idempotentRetryRegistry = buildIdempotentRetryRegistry(props);
        this.circuitBreakerRegistry = buildCircuitBreakerRegistry(props);
        this.rateLimiterRegistry = buildRateLimiterRegistry(props);
        this.timeLimiterRegistry = buildTimeLimiterRegistry(props);

        log.info("ToolResilienceConfig 初始化完成: networkRetry.maxAttempts={}, idempotentRetry.maxAttempts={}, "
                        + "cb.failureRateThreshold={}%, rl.limitForPeriod={}, tl.timeoutSeconds={}",
                props.getRetry().getMaxAttempts(),
                props.getRetry().getMaxAttempts(),
                props.getCircuitbreaker().getFailureRateThreshold(),
                props.getRatelimiter().getLimitForPeriod(),
                props.getTimelimiter().getTimeoutSeconds());
    }

    /**
     * 网络级重试：所有工具都使用。
     * 只重试连接级错误（连接失败、连接超时、DNS 失败），这些错误说明请求根本没到达下游。
     */
    private RetryRegistry buildNetworkRetryRegistry(ReactorConfig.ResilienceProps props) {
        ReactorConfig.RetryProps rp = props.getRetry();

        RetryConfig config = RetryConfig.custom()
                .maxAttempts(rp.getMaxAttempts())
                .intervalFunction(IntervalFunction.ofExponentialBackoff(
                        Duration.ofMillis(rp.getWaitDurationMs()),
                        rp.getBackoffMultiplier(),
                        Duration.ofSeconds(30)))
                .retryExceptions(
                        ConnectException.class,
                        java.net.UnknownHostException.class
                )
                .ignoreExceptions(
                        IllegalArgumentException.class
                )
                .build();

        return RetryRegistry.of(config);
    }

    /**
     * 幂等重试：仅幂等工具使用。
     * 除了连接级错误，还重试读超时和 5xx 服务端错误。
     * 因为幂等工具重复执行不会有副作用。
     */
    private RetryRegistry buildIdempotentRetryRegistry(ReactorConfig.ResilienceProps props) {
        ReactorConfig.RetryProps rp = props.getRetry();

        RetryConfig config = RetryConfig.custom()
                .maxAttempts(rp.getMaxAttempts())
                .intervalFunction(IntervalFunction.ofExponentialBackoff(
                        Duration.ofMillis(rp.getWaitDurationMs()),
                        rp.getBackoffMultiplier(),
                        Duration.ofSeconds(30)))
                .retryExceptions(
                        IOException.class,
                        SocketTimeoutException.class,
                        ConnectException.class,
                        TimeoutException.class,
                        IllegalStateException.class
                )
                .ignoreExceptions(
                        IllegalArgumentException.class
                )
                .build();

        return RetryRegistry.of(config);
    }

    private CircuitBreakerRegistry buildCircuitBreakerRegistry(ReactorConfig.ResilienceProps props) {
        ReactorConfig.CircuitBreakerProps cbp = props.getCircuitbreaker();

        CircuitBreakerConfig config = CircuitBreakerConfig.custom()
                .slidingWindowSize(cbp.getSlidingWindowSize())
                .failureRateThreshold(cbp.getFailureRateThreshold())
                .waitDurationInOpenState(Duration.ofSeconds(cbp.getWaitDurationOpenSeconds()))
                .permittedNumberOfCallsInHalfOpenState(3)
                .slowCallDurationThreshold(Duration.ofSeconds(60))
                .slowCallRateThreshold(80.0f)
                .build();

        return CircuitBreakerRegistry.of(config);
    }

    private RateLimiterRegistry buildRateLimiterRegistry(ReactorConfig.ResilienceProps props) {
        ReactorConfig.RateLimiterProps rlp = props.getRatelimiter();

        RateLimiterConfig config = RateLimiterConfig.custom()
                .limitForPeriod(rlp.getLimitForPeriod())
                .limitRefreshPeriod(Duration.ofSeconds(rlp.getLimitRefreshPeriodSeconds()))
                .timeoutDuration(Duration.ofSeconds(5))
                .build();

        return RateLimiterRegistry.of(config);
    }

    private TimeLimiterRegistry buildTimeLimiterRegistry(ReactorConfig.ResilienceProps props) {
        ReactorConfig.TimeLimiterProps tlp = props.getTimelimiter();

        TimeLimiterConfig config = TimeLimiterConfig.custom()
                .timeoutDuration(Duration.ofSeconds(tlp.getTimeoutSeconds()))
                .cancelRunningFuture(true)
                .build();

        return TimeLimiterRegistry.of(config);
    }
}
