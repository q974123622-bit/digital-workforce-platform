type BrandMarkProps = {
  size?: number;
  inverse?: boolean;
  className?: string;
};

/** 原创AI员工辅助标识，不复刻或替代公司正式司徽。 */
export default function BrandMark({ size = 36, inverse = false, className }: BrandMarkProps) {
  const ink = inverse ? '#ffffff' : 'var(--dwp-brand)';
  return (
    <svg aria-label="AI员工工作台" className={className} height={size} role="img" viewBox="0 0 48 48" width={size} xmlns="http://www.w3.org/2000/svg">
      <rect fill={inverse ? 'rgba(255,255,255,.12)' : 'var(--dwp-brand-soft)'} height="48" rx="14" width="48" />
      <path d="M13 27.5c0-7.2 5-13 11.3-13 5.2 0 9.6 3.9 10.9 9.3" fill="none" stroke={ink} strokeLinecap="round" strokeWidth="3" />
      <path d="M35 19v6.8h-6.8" fill="none" stroke="var(--dwp-accent)" strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" />
      <circle cx="16" cy="31" fill="var(--dwp-accent)" r="4.2" />
      <circle cx="31.5" cy="31" fill={ink} r="4.2" />
      <path d="M20.5 31h6.7" stroke={ink} strokeLinecap="round" strokeWidth="2.6" />
    </svg>
  );
}
