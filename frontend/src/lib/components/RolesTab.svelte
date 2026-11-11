<script>
  /** the aircraft-role reference, rendered inside the reader
   * rather than at a separate address.
   *
   * Two reasons it is not the generated HTML page in an iframe or a fetch:
   * it has to sit in the reader's own layout (beside the panel on desktop,
   * full-screen on mobile), and it interleaves LIVE aircraft counts that a
   * pre-rendered blob cannot carry.
   *
   * The taxonomy still comes from one generated artifact
   * (/roles.reference.json, rendered from data/in/config/roles.json by
   * backend/pipeline/gen/roles_page.py's render_json(), a `./run gen
   * roles` pipeline step tested against the live taxonomy), so this view
   * cannot describe a mapping the pipeline does not perform.
   */
  // data: the generated taxonomy; counts: computeTypeCounts() output.
  let { data = null, counts = null, contactUrl = '' } = $props();

  // Filtered-vs-whole-population is the whole point of the counts, so the
  // header says which is which rather than leaving two bare numbers.
  let totals = $derived(counts?.totals || null);
  let filtersNarrow = $derived(totals && totals.filtered < totals.all);

  function countFor(code) {
    return counts?.byCode?.[code] || null;
  }

  function pct(c) {
    if (!c || !c.all) return null;
    return Math.round((c.filtered / c.all) * 100);
  }

  let query = $state('');
  let q = $derived(query.trim().toUpperCase());
  function matches(code, label) {
    if (!q) return true;
    return code.includes(q) || (label || '').toUpperCase().includes(q);
  }
</script>

{#if !data}
  <p class="roles-loading">Loading the role taxonomy…</p>
{:else}
  <div class="roles-tab">
    <p class="roles-lead">
      The role filter groups aircraft by what they are for. This shows exactly how
      that mapping is made, for every one of the {data.designators.length} type codes
      in the taxonomy. It is generated from the same configuration file the data
      pipeline reads, so it cannot describe anything the pipeline does not do.
    </p>

    <div class="roles-disclaimer" role="note">
      <strong>Please correct this.</strong>
      This taxonomy was assembled with AI assistance, and the authors are very
      uncertain about it. Role assignments for military aircraft are a matter of
      doctrine, variant and operator, and one type code frequently covers airframes
      doing quite different jobs. We expect errors here. Corrections from
      subject-matter experts are wanted &mdash; ideally with a reference to public
      documentation we can cite, so the correction can be verified rather than
      simply believed.
      {#if contactUrl}
        <a href={contactUrl} target="_blank" rel="noopener">Send a correction</a>.
      {/if}
    </div>

    {#if totals}
      <div class="roles-countkey">
        <span class="ck-all">{totals.all.toLocaleString()}</span> airframes in the
        whole dataset;
        <span class="ck-filtered">{totals.filtered.toLocaleString()}</span>
        under your current filters{#if filtersNarrow}
          &nbsp;&mdash; every row below reads
          <span class="ck-filtered">filtered</span>&thinsp;/&thinsp;<span class="ck-all">all</span>
        {:else}
          &nbsp;(nothing is currently filtered out)
        {/if}.
      </div>
    {/if}

    <label class="roles-search">
      <span class="visually-hidden">Filter this list</span>
      <input type="search" bind:value={query}
        placeholder="Find a type code or aircraft…" />
    </label>

    {#each data.groups as group (group.slug)}
      <section class="role-group">
        <h3>
          {group.label}
          <span class="group-flag">{group.default_on ? 'on by default' : 'off by default'}</span>
        </h3>
        {#each group.roles as role (role.slug)}
          {@const shown = role.codes.filter(c =>
            matches(c, data.designators.find(d => d.code === c)?.label))}
          {#if shown.length || !q}
            <div class="role">
              <h4>{role.label} <code>{role.slug}</code></h4>
              {#if role.desc}<p class="role-desc">{role.desc}</p>{/if}
              {#if shown.length}
                <ul class="codes">
                  {#each shown as code (code)}
                    {@const c = countFor(code)}
                    {@const d = data.designators.find(x => x.code === code)}
                    <li>
                      <code class="tc">{code}</code>
                      <span class="tc-label">{d?.label || ''}</span>
                      {#if c}
                        <span class="tc-count"
                          class:none={c.filtered === 0}
                          title="{c.filtered} of {c.all} airframes of this type pass your current filters">
                          <span class="ck-filtered">{c.filtered}</span><span class="slash">/</span><span class="ck-all">{c.all}</span>
                          {#if c.all}<span class="tc-pct">{pct(c)}%</span>{/if}
                        </span>
                      {:else}
                        <span class="tc-count absent" title="No airframes of this type appear in the data at all">&mdash;</span>
                      {/if}
                    </li>
                  {/each}
                </ul>
              {:else}
                <p class="no-types">No type codes map to this role in the current taxonomy.</p>
              {/if}
            </div>
          {/if}
        {/each}
      </section>
    {/each}

    <section class="role-group">
      <h3>Excluded from the data entirely</h3>
      <p class="role-desc">
        Aircraft whose type could fill any of these roles are dropped before anything
        else happens, so they appear in no map, count or export. The exclusion is
        conservative: if <em>any</em> role the type could fill is in this set, the
        aircraft is excluded. If you are looking for helicopters and finding none,
        this is why &mdash; not an absence in the data.
      </p>
      <ul class="codes">
        {#each data.excluded as ex (ex.slug)}
          <li>
            <code class="tc">{ex.slug}</code>
            <span class="tc-label">{ex.label} &mdash; {ex.why}, {ex.codes.length} type code{ex.codes.length === 1 ? '' : 's'}</span>
          </li>
        {/each}
      </ul>
    </section>

    <section class="role-group">
      <h3>When the role is unknown</h3>
      <ul class="codes">
        {#each data.unknown_buckets as bucket (bucket.slug)}
          <li><code class="tc">{bucket.slug}</code><span class="tc-label">{bucket.meaning}</span></li>
        {/each}
      </ul>
    </section>

    {#if data.designators.some(d => d.variants.length)}
      <section class="role-group">
        <h3>Variant narrowing</h3>
        <p class="role-desc">
          Some type codes cover a family doing different jobs. Where the
          aircraft&rsquo;s description identifies the variant, it narrows the roles to
          just those listed. <strong>Order matters</strong> &mdash; patterns are matched
          as substrings against the upper-cased description and the <em>first</em> match
          wins, so these are in taxonomy order, not alphabetical.
        </p>
        {#each data.designators.filter(d => d.variants.length && matches(d.code, d.label)) as d (d.code)}
          <div class="variant">
            <code class="tc">{d.code}</code> <span class="tc-label">{d.label}</span>
            <ul class="variant-rules">
              {#each d.variants as v}
                <li>description contains <code>{v.pattern}</code> &rarr; {v.roles.join(', ')}</li>
              {/each}
            </ul>
          </div>
        {/each}
      </section>
    {/if}
  </div>
{/if}

<style>
  .roles-tab { font-size: 13px; line-height: 1.5; color: #c8ced4; }
  .roles-loading { color: #9a9a9a; font-style: italic; }
  .roles-lead { margin-bottom: 12px; }

  .roles-disclaimer {
    margin: 0 0 14px;
    padding: 11px 13px;
    background: rgba(200, 120, 40, 0.10);
    border: 1px solid #7a5a2a;
    border-left: 4px solid #c8862a;
    border-radius: 5px;
    font-size: 12.5px;
  }
  .roles-disclaimer strong { color: #f0c886; }
  .roles-disclaimer a { color: #6ab4f0; }

  .roles-countkey {
    margin-bottom: 10px;
    padding: 8px 10px;
    background: rgba(255, 255, 255, 0.04);
    border-left: 3px solid #4a8abf;
    border-radius: 3px;
    font-size: 12px;
  }
  .ck-filtered { color: #6ab4f0; font-weight: 700; }
  .ck-all { color: #e0e0e0; font-weight: 700; }

  .roles-search { display: block; margin-bottom: 14px; }
  .roles-search input {
    width: 100%;
    padding: 6px 9px;
    font-family: inherit;
    font-size: 12px;
    color: #e0e0e0;
    background: #14171c;
    border: 1px solid #333a44;
    border-radius: 4px;
  }
  .roles-search input:focus-visible { outline: 2px solid #6ab4f0; outline-offset: 1px; }
  .visually-hidden {
    position: absolute; width: 1px; height: 1px; overflow: hidden;
    clip: rect(0 0 0 0); white-space: nowrap;
  }

  .role-group { margin-bottom: 18px; }
  .role-group h3 {
    font-size: 14px;
    color: #cfe3f5;
    margin: 16px 0 6px;
    padding-bottom: 4px;
    border-bottom: 1px solid #262c34;
  }
  .group-flag { font-size: 11px; font-weight: 400; color: #8a8a8a; }
  .role { margin-bottom: 10px; }
  .role h4 { font-size: 13px; color: #e8e8e8; margin: 10px 0 2px; }
  .role-desc { font-size: 12px; color: #a8aeb4; margin-bottom: 4px; }
  .no-types { font-size: 12px; color: #8a8a8a; font-style: italic; }

  code {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.88em;
    background: rgba(255, 255, 255, 0.07);
    padding: 1px 4px;
    border-radius: 3px;
    color: #9fc6e8;
  }

  ul.codes { list-style: none; margin: 0; padding: 0; }
  ul.codes li {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 3px 0;
    border-bottom: 1px solid #1e2228;
  }
  .tc { flex-shrink: 0; }
  .tc-label { flex: 1 1 auto; min-width: 0; color: #a8aeb4; font-size: 12px; }
  .tc-count {
    flex-shrink: 0;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
    white-space: nowrap;
  }
  .tc-count .slash { color: #6a6a6a; margin: 0 1px; }
  .tc-count .tc-pct { color: #8a8a8a; margin-left: 5px; font-size: 11px; }
  /* A type filtered down to nothing is the most useful signal on the page —
     it is the row that says "your current selection removed this entirely". */
  .tc-count.none .ck-filtered { color: #d98a8a; }
  .tc-count.absent { color: #6a6a6a; }

  .variant { margin-bottom: 8px; }
  ul.variant-rules { list-style: none; margin: 2px 0 0 14px; padding: 0; font-size: 12px; color: #a8aeb4; }
  ul.variant-rules li { padding: 1px 0; }
</style>
