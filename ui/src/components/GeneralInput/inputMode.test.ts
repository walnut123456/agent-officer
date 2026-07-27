import { describe, expect, it } from "vitest";

import { buildSubmitPayload } from "./inputMode";

describe("inputMode", () => {
  it("深度研究模式应保留结构化输出类型，不再透传 deepThink", () => {
    const result = buildSubmitPayload({
      question: "帮我调研竞品",
      visibleMode: "research",
      isDataAgent: false,
      visibleOutputProduct: { type: "html" } as CHAT.Product,
      uploadedFiles: [],
      chatRole: null,
    });
    expect(result).toMatchObject({
      outputStyle: "html",
    });
    // deepThink 不再由前端传入后端，AgentType 由服务端 RouterService 自动判断
    expect(result).not.toHaveProperty("deepThink");
  });
});