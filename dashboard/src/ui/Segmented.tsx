import "./ui.css";

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  count?: number;
}

/**
 * The filter control on a card's header: Running 13 / Burned 1 / Finished 3
 * / All 17. Each option is a pressed or unpressed button.
 */
export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  /** What the group filters, for assistive technology ("Show"). */
  label: string;
  options: SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
          {option.count !== undefined && (
            <>
              {" "}
              <span className="segmented-count num">{option.count}</span>
            </>
          )}
        </button>
      ))}
    </div>
  );
}
