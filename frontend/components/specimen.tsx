import { SPECIMEN } from "@/lib/specimen";

/** The headline, carried out on one building.
 *
 * Six rows and one quotation. Everything the longer version said in prose —
 * how many rules reached each obligation, which fact blocked the rest, how the
 * address resolved — is in the app, one click past the sign-in below it. The
 * front door only has to show that the thing answers, dates its answer, and
 * quotes its source.
 *
 * The unknown row stays. A panel that answered six out of six would be hiding
 * the facts the public record does not carry.
 */
export function Specimen() {
  return (
    <figure className="spec">
      <figcaption className="spec-head">
        <p className="spec-address">
          {SPECIMEN.address}
          <span>, {SPECIMEN.locality}</span>
        </p>
        <p className="spec-asof">
          as of <strong className="mono">{SPECIMEN.asOf}</strong>
        </p>
      </figcaption>

      <ul className="spec-rows">
        {SPECIMEN.rows.map((row) => (
          <li key={row.category}>
            <span className="spec-cat">{row.category}</span>
            <span className="spec-answer">{row.answer}</span>
            <span className="spec-result">
              <span
                aria-hidden
                className="spec-dot"
                style={{
                  background:
                    row.result === "applies" ? "var(--good)" : "var(--warn)",
                }}
              />
              {row.result}
            </span>
          </li>
        ))}
      </ul>

      <div className="spec-foot">
        <blockquote>{SPECIMEN.quote}</blockquote>
        <p>
          <a href={SPECIMEN.sourceUrl} target="_blank" rel="noreferrer">
            {SPECIMEN.citation}
          </a>
          <span>one recorded run, not a live query</span>
        </p>
      </div>
    </figure>
  );
}
