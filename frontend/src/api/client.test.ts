import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, AUTH_EXPIRED_EVENT } from './client';

const reply = (ok: boolean, status: number, body: unknown) => ({
  ok,
  status,
  statusText: ok ? 'OK' : 'Unauthorized',
  json: vi.fn().mockResolvedValue(body),
});

describe('API authentication recovery', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('dispatches one auth-expired event for repeated 401 responses', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(reply(true, 200, { account: { username: 'E10281' } }))
      .mockResolvedValue(reply(false, 401, { error: { message: '登录已过期' } }));
    vi.stubGlobal('fetch', fetchMock);
    await api.login('E10281', 'temporary-password');

    const onExpired = vi.fn();
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    await expect(api.me()).rejects.toThrow('登录已过期');
    await expect(api.me()).rejects.toThrow('登录已过期');
    window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);

    expect(onExpired).toHaveBeenCalledTimes(1);
  });

  it('does not treat an ordinary network failure as authentication expiry', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network unavailable')));
    const onExpired = vi.fn();
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    await expect(api.me()).rejects.toThrow('network unavailable');
    window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);

    expect(onExpired).not.toHaveBeenCalled();
  });
});
