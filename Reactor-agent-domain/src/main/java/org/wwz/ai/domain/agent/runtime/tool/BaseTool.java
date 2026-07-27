package org.wwz.ai.domain.agent.runtime.tool;

import java.util.Map;

/**
 * 工具基接口
 */
public interface BaseTool {
    String getName();

    String getDescription();

    Map<String, Object> toParams();

    Object execute(Object input);

    /**
     * 工具是否幂等（安全重试）。
     * 默认 false，只有只读/幂等工具覆写返回 true。
     * 框架据此决定是否对工具调用做自动重试（指数退避）。
     */
    default boolean isIdempotent() {
        return false;
    }
}