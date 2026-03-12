import { Card, CardContent } from "./ui/card";

export const KpiCard = ({ title, value, helper, testId }) => {
  return (
    <Card className="premium-card border-none shadow-none" data-testid={testId}>
      <CardContent className="space-y-2 p-5">
        <p className="text-sm text-slate-500/90" data-testid={`${testId}-title`}>
          {title}
        </p>
        <p className="score-count text-3xl font-semibold text-slate-900" data-testid={`${testId}-value`}>
          {value}
        </p>
        <p className="text-xs text-slate-500" data-testid={`${testId}-helper`}>
          {helper}
        </p>
      </CardContent>
    </Card>
  );
};
