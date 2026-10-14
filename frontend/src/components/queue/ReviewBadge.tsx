import { reviewBadge } from '../../lib/review';
import type { ReviewInfo } from '../../types/rules';

/**
 * Where a delivered final video stands with its reviewer (T-F): pending / approved /
 * changes requested. Hover shows the reason. Display only: deciding is done with
 * `review approve|reject` (command line) or the API, not from this screen.
 */
export function ReviewBadge({ review }: { review: ReviewInfo | null | undefined }) {
  if (!review) return null;
  const b = reviewBadge(review);
  return (
    <span
      title={b.title}
      className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${b.cls}`}
      data-review={review.status}
    >
      {b.label}
    </span>
  );
}
