interface Props {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
}

export function Pagination({ page, pageSize, total, onPage }: Props) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : page * pageSize + 1;
  const to = Math.min(total, (page + 1) * pageSize);
  const btn = "rounded-md px-3 py-1.5 text-[13px] ring-1 ring-line enabled:hover:bg-raised disabled:cursor-not-allowed disabled:opacity-40";
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between gap-4">
      <p className="num text-[13px] text-muted">
        {from}–{to} sur {total} reload{total > 1 ? "s" : ""} · page {page + 1}/{pages}
      </p>
      <div className="flex gap-2">
        <button className={btn} disabled={page === 0} onClick={() => onPage(page - 1)}>
          Précédent
        </button>
        <button className={btn} disabled={page + 1 >= pages} onClick={() => onPage(page + 1)}>
          Suivant
        </button>
      </div>
    </nav>
  );
}
