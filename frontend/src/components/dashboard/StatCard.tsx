/**
 * frontend/src/components/dashboard/StatCard.tsx
 * Reusable metric card — label, big v  alue, optional sub-label and icon.
 */
interface StatCardProps {
  label: string;
  value: string | number;
  valueClass?: string;
  sub?: string;
  icon?: React.ReactNode;
}

export function StatCard({ label, value, valueClass, sub, icon }: StatCardProps) {
  return (
    <div className="card p-4">
      <div className="flex items-start justify-between">
        <p className="section-label mb-2">{label}</p>
        {icon && <div className="opacity-60">{icon}</div>}
      </div>
      <p className={`text-2xl font-bold tracking-tight ${valueClass ?? "text-gray-100"}`}>{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-1">{sub}</p>}
    </div>
  );
}