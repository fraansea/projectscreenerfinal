export const ScoreBadge = ({ score, testId }) => {
  const rounded = Number(score || 0).toFixed(1);

  const classes =
    score >= 80
      ? "border-green-200 bg-green-50 text-green-700"
      : score >= 60
        ? "border-amber-200 bg-amber-50 text-amber-700"
        : "border-red-200 bg-red-50 text-red-700";

  return (
    <span
      className={`inline-flex min-w-[82px] items-center justify-center rounded-full border px-3 py-1 text-xs font-semibold ${classes}`}
      data-testid={testId}
    >
      {rounded}%
    </span>
  );
};
