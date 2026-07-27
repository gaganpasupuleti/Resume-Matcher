import { EMBED_PROTOCOL } from './constants';
import { isAllowedParentOrigin } from './allowed-origins';

export type EmbedReadyMessage = {
  protocol: typeof EMBED_PROTOCOL;
  type: 'ready';
  app: 'resume-matcher';
};

/** Optional parent identity — no secrets, no tokens. */
export type EmbedParentMessage = {
  protocol: typeof EMBED_PROTOCOL;
  type: 'parent';
  parent: 'codequest';
};

export type EmbedParentAckMessage = {
  protocol: typeof EMBED_PROTOCOL;
  type: 'parent-ack';
};

export type EmbedIncomingMessage = EmbedParentMessage;

export function buildReadyMessage(): EmbedReadyMessage {
  return {
    protocol: EMBED_PROTOCOL,
    type: 'ready',
    app: 'resume-matcher',
  };
}

export function buildParentAckMessage(): EmbedParentAckMessage {
  return {
    protocol: EMBED_PROTOCOL,
    type: 'parent-ack',
  };
}

export function parseIncomingEmbedMessage(data: unknown): EmbedIncomingMessage | null {
  if (!data || typeof data !== 'object') return null;
  const msg = data as Record<string, unknown>;
  if (msg.protocol !== EMBED_PROTOCOL) return null;
  if (msg.type !== 'parent') return null;
  if (msg.parent !== 'codequest') return null;
  return {
    protocol: EMBED_PROTOCOL,
    type: 'parent',
    parent: 'codequest',
  };
}

/**
 * Accept parent identity only from an allowlisted origin.
 * Returns the ack payload to send, or null if the event must be ignored.
 */
export function handleParentMessageEvent(
  event: Pick<MessageEvent, 'origin' | 'data'>,
  allowlist: string[]
): EmbedParentAckMessage | null {
  if (!isAllowedParentOrigin(event.origin, allowlist)) {
    return null;
  }
  const parsed = parseIncomingEmbedMessage(event.data);
  if (!parsed) return null;
  return buildParentAckMessage();
}

export function postToParent(
  message: EmbedReadyMessage | EmbedParentAckMessage,
  targetOrigin: string,
  parentWindow: Window | null = typeof window !== 'undefined' ? window.parent : null
): boolean {
  if (!parentWindow || parentWindow === (typeof window !== 'undefined' ? window : null)) {
    return false;
  }
  if (!targetOrigin || targetOrigin === '*') {
    return false;
  }
  parentWindow.postMessage(message, targetOrigin);
  return true;
}
