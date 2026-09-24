import { describe, expect, it } from 'vitest';
import { parseServerTime } from './serverTime';
import { formatDuration } from './duration';

describe('parseServerTime', () => {
  it('无时区标识的时间戳按 UTC 解析（不受浏览器时区影响）', () => {
    expect(parseServerTime('2026-09-16T01:35:15.879767')).toBe(
      Date.parse('2026-09-16T01:35:15.879767Z'),
    );
  });

  it('已带时区标识的时间戳原样透传', () => {
    expect(parseServerTime('2026-09-16T01:35:15+08:00')).toBe(
      Date.parse('2026-09-16T01:35:15+08:00'),
    );
    expect(parseServerTime('2026-09-16T01:35:15Z')).toBe(Date.parse('2026-09-16T01:35:15Z'));
  });

  it('空值返回 undefined', () => {
    expect(parseServerTime(undefined)).toBeUndefined();
    expect(parseServerTime(null)).toBeUndefined();
    expect(parseServerTime('')).toBeUndefined();
  });

  it('两个服务端时间戳之差等于真实耗时（不会多出时区差）', () => {
    const start = parseServerTime('2026-09-16T01:35:15.879767')!;
    const end = parseServerTime('2026-09-16T01:35:49.664111')!;
    expect(Math.round((end - start) / 1000)).toBe(34);
  });

  it('服务端开始时间与客户端当前时间混算不会多出时区偏移', () => {
    // 回归用例：修复前这里会得到 28842 秒（8 小时 + 真实耗时）。
    const start = parseServerTime('2026-09-16T01:35:15.879767')!;
    const clientNow = Date.parse('2026-09-16T01:35:57.000Z');
    expect(Math.round((clientNow - start) / 1000)).toBe(41);
  });
});

describe('formatDuration', () => {
  it('一分钟以内保留一位小数', () => {
    expect(formatDuration(0)).toBe('0.0s');
    expect(formatDuration(7700)).toBe('7.7s');
    expect(formatDuration(59900)).toBe('59.9s');
  });

  it('超过一分钟改用分钟显示', () => {
    expect(formatDuration(83000)).toBe('1m23s');
  });

  it('异常大的数值不会显示成裸秒数', () => {
    expect(formatDuration(28842000)).toBe('8h0m');
  });

  it('非法数值按 0 处理', () => {
    expect(formatDuration(Number.NaN)).toBe('0.0s');
    expect(formatDuration(-5)).toBe('0.0s');
  });
});
