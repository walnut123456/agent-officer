type InputModeKey = "quick" | "think" | "research";

export function buildSubmitPayload(params: {
  question: string;
  visibleMode: InputModeKey;
  isDataAgent: boolean;
  visibleOutputProduct: CHAT.Product;
  uploadedFiles: CHAT.TFile[];
  chatRole: CHAT.ConversationRole | null;
}) {
  const outputStyle = params.isDataAgent
    ? "dataAgent"
    : params.visibleMode === "quick"
      ? "chat"
      : params.visibleOutputProduct.type;

  return {
    message: params.question.trim(),
    outputStyle,
    // deepThink 不再传入后端——AgentType 由服务端 RouterService 根据 query 内容自动判断
    files: params.uploadedFiles.length > 0 ? params.uploadedFiles : undefined,
    aiAgentId: outputStyle === "chat" ? params.chatRole?.agentId : undefined,
  };
}
