import { describe, expect, it, vi } from 'vitest';
import {
  EMBED_PROTOCOL,
  buildReadyMessage,
  handleParentMessageEvent,
  parseIncomingEmbedMessage,
  postToParent,
} from '@/lib/embed';

const ALLOWLIST = ['http://localhost:5000', 'http://127.0.0.1:5000'];

describe('embed postMessage handshake', () => {
  it('builds ready message without secrets', () => {
    const ready = buildReadyMessage();
    expect(ready).toEqual({
      protocol: EMBED_PROTOCOL,
      type: 'ready',
      app: 'resume-matcher',
    });
    expect(JSON.stringify(ready)).not.toMatch(/token|secret|password|jwt|api[_-]?key/i);
  });

  it('parses optional parent identity', () => {
    expect(
      parseIncomingEmbedMessage({
        protocol: EMBED_PROTOCOL,
        type: 'parent',
        parent: 'codequest',
      })
    ).toEqual({
      protocol: EMBED_PROTOCOL,
      type: 'parent',
      parent: 'codequest',
    });
  });

  it('rejects malformed or secret-bearing parent payloads as identity', () => {
    expect(parseIncomingEmbedMessage({ protocol: EMBED_PROTOCOL, type: 'parent' })).toBeNull();
    expect(
      parseIncomingEmbedMessage({
        protocol: 'codequest-ai/v1',
        type: 'hello',
        sessionNonce: 'abc',
      })
    ).toBeNull();
  });

  it('acks parent identity only from allowlisted origin', () => {
    const ack = handleParentMessageEvent(
      {
        origin: 'http://localhost:5000',
        data: { protocol: EMBED_PROTOCOL, type: 'parent', parent: 'codequest' },
      },
      ALLOWLIST
    );
    expect(ack).toEqual({ protocol: EMBED_PROTOCOL, type: 'parent-ack' });
  });

  it('rejects wrong parent origin', () => {
    const ack = handleParentMessageEvent(
      {
        origin: 'https://evil.example',
        data: { protocol: EMBED_PROTOCOL, type: 'parent', parent: 'codequest' },
      },
      ALLOWLIST
    );
    expect(ack).toBeNull();
  });

  it('never posts ready to wildcard origin', () => {
    const parent = { postMessage: vi.fn() } as unknown as Window;
    expect(postToParent(buildReadyMessage(), '*', parent)).toBe(false);
    expect(parent.postMessage).not.toHaveBeenCalled();
  });

  it('posts ready to validated parent origin', () => {
    const parent = { postMessage: vi.fn() } as unknown as Window;
    expect(postToParent(buildReadyMessage(), 'http://localhost:5000', parent)).toBe(true);
    expect(parent.postMessage).toHaveBeenCalledWith(buildReadyMessage(), 'http://localhost:5000');
  });
});
