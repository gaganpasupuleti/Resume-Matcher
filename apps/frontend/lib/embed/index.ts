export {
  EMBED_PROTOCOL,
  EMBED_QUERY_KEY,
  EMBED_QUERY_VALUE,
  EMBED_PARENT_ORIGIN_QUERY_KEY,
  EMBED_SESSION_KEY,
  DEFAULT_EMBED_PARENT_ORIGINS,
} from './constants';
export {
  getAllowedParentOrigins,
  isAllowedParentOrigin,
  resolveParentTargetOrigin,
} from './allowed-origins';
export {
  detectEmbedMode,
  isEmbedFlagInHash,
  isEmbedFlagInSearch,
  pickParentTargetOrigin,
  readEmbedSessionFlag,
  readParentOriginCandidate,
  withEmbedParams,
  writeEmbedSessionFlag,
} from './detect';
export {
  buildParentAckMessage,
  buildReadyMessage,
  handleParentMessageEvent,
  parseIncomingEmbedMessage,
  postToParent,
  type EmbedIncomingMessage,
  type EmbedParentAckMessage,
  type EmbedParentMessage,
  type EmbedReadyMessage,
} from './handshake';
