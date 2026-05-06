// Tiny class merger. Accepts strings, arrays, conditionals, and objects.
// No clsx/tailwind-merge dependency — we don't need conflict resolution.
type ClassValue = string | number | null | false | undefined | ClassValue[] | Record<string, unknown>;

export function cn(...inputs: ClassValue[]): string {
  const out: string[] = [];
  const walk = (val: ClassValue) => {
    if (!val) return;
    if (typeof val === 'string' || typeof val === 'number') {
      out.push(String(val));
      return;
    }
    if (Array.isArray(val)) {
      val.forEach(walk);
      return;
    }
    if (typeof val === 'object') {
      for (const key in val) {
        if (val[key]) out.push(key);
      }
    }
  };
  inputs.forEach(walk);
  return out.join(' ');
}
