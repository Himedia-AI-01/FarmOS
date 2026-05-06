import { cn } from '@/lib/cn';
import type { CSSProperties, HTMLAttributes } from 'react';

type Shape = 'rect' | 'pill' | 'circle' | 'text';

interface SkeletonProps extends HTMLAttributes<HTMLDivElement> {
  shape?: Shape;
  width?: string | number;
  height?: string | number;
  /** Render N stacked text-line skeletons; only used when shape === 'text'. */
  lines?: number;
  /** Loading announcement; pass empty string to suppress. */
  label?: string;
}

const SHAPE: Record<Shape, string> = {
  rect: 'rounded-[10px]',
  pill: 'rounded-full',
  circle: 'rounded-full aspect-square',
  text: 'rounded-[6px] h-[0.85em]',
};

export default function Skeleton({
  shape = 'rect',
  width,
  height,
  lines,
  label = '불러오는 중',
  className,
  style,
  ...rest
}: SkeletonProps) {
  const sizeStyle: CSSProperties = {
    width: typeof width === 'number' ? `${width}px` : width,
    height: typeof height === 'number' ? `${height}px` : height,
    ...style,
  };

  if (shape === 'text' && lines && lines > 1) {
    return (
      <div
        role="status"
        aria-live="polite"
        aria-busy="true"
        className={cn('flex flex-col gap-2', className)}
        {...rest}
      >
        {Array.from({ length: lines }).map((_, i) => (
          <div
            key={i}
            className={cn('skeleton', SHAPE.text)}
            style={{ width: i === lines - 1 ? '70%' : '100%' }}
          />
        ))}
        {label && <span className="sr-only">{label}</span>}
      </div>
    );
  }

  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      className={cn('skeleton', SHAPE[shape], className)}
      style={sizeStyle}
      {...rest}
    >
      {label && <span className="sr-only">{label}</span>}
    </div>
  );
}
