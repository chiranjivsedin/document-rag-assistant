import type { TagCategory } from "../../api/types";

/** One extra category: a dropdown when a document takes one value, check boxes when it may take several. */
export function ExtraTagField({
  category,
  chosen,
  onChange,
  idPrefix,
  keepIds = [],
}: {
  category: TagCategory;
  chosen: number[];
  onChange: (ids: number[]) => void;
  idPrefix: string;
  keepIds?: number[]; // retired values the document already has: still offered, so they can stay
}) {
  const offered = category.values.filter((value) => value.active || keepIds.includes(value.id));
  const id = `${idPrefix}-${category.key}`;
  if (!category.multiple) {
    const current = chosen.find((value) => offered.some((v) => v.id === value));
    return (
      <div className="form-field">
        <label htmlFor={id}>{category.label}</label>
        <select
          id={id}
          className="select-field"
          value={current === undefined ? "" : String(current)}
          onChange={(event) => onChange(event.target.value ? [Number(event.target.value)] : [])}
        >
          <option value="">None</option>
          {offered.map((value) => (
            <option key={value.id} value={value.id}>
              {value.active ? value.label : `${value.label} (retired)`}
            </option>
          ))}
        </select>
      </div>
    );
  }
  return (
    <fieldset className="form-field tag-choices">
      <legend>{category.label}</legend>
      {offered.length === 0 && <span className="hint">No values yet.</span>}
      {offered.map((value) => (
        <label key={value.id} className="tag-choice">
          <input
            type="checkbox"
            checked={chosen.includes(value.id)}
            onChange={(event) =>
              onChange(event.target.checked ? [...chosen, value.id] : chosen.filter((other) => other !== value.id))
            }
          />
          {value.active ? value.label : `${value.label} (retired)`}
        </label>
      ))}
    </fieldset>
  );
}
