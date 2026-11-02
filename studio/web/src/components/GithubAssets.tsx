import { GithubMark } from "./GithubMark";

export type ReleaseAsset = {
  name: string;
  kind: string;
  download: string;
  size: number;
};

export function GithubAssets({
  assets,
  busy,
  emptyLabel,
  folderLabel,
  deckLabel,
  deleteLabel,
  confirmTemplate,
  onDelete,
}: {
  assets: ReleaseAsset[];
  busy: boolean;
  emptyLabel: string;
  folderLabel: string;
  deckLabel: string;
  deleteLabel: string;
  confirmTemplate: string;
  onDelete: (name: string) => void;
}) {
  if (!assets.length) {
    return <p className="lede">{emptyLabel}</p>;
  }
  return (
    <ul className="gh-assets">
      {assets.map((item) => (
        <li key={item.name} className="gh-asset">
          <GithubMark />
          <span className="gh-kind">{item.kind === "folder" ? folderLabel : deckLabel}</span>
          {item.download ? (
            <a href={item.download} target="_blank" rel="noreferrer">
              {item.name}
            </a>
          ) : (
            <strong>{item.name}</strong>
          )}
          <button
            className="btn ghost danger"
            type="button"
            disabled={busy}
            onClick={() => {
              const message = confirmTemplate.replace("{name}", item.name);
              if (window.confirm(message)) onDelete(item.name);
            }}
          >
            {deleteLabel}
          </button>
        </li>
      ))}
    </ul>
  );
}
