import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

type Props = {
  content: string;
};

const components: Components = {
  h1: ({ node: _node, ...props }) => (
    <h1 className="mb-3 mt-5 text-xl font-semibold first:mt-0" {...props} />
  ),
  h2: ({ node: _node, ...props }) => (
    <h2 className="mb-2.5 mt-5 text-lg font-semibold first:mt-0" {...props} />
  ),
  h3: ({ node: _node, ...props }) => (
    <h3 className="mb-2 mt-4 text-base font-semibold first:mt-0" {...props} />
  ),
  h4: ({ node: _node, ...props }) => (
    <h4 className="mb-2 mt-4 text-sm font-semibold first:mt-0" {...props} />
  ),
  p: ({ node: _node, ...props }) => (
    <p className="my-2 leading-relaxed first:mt-0 last:mb-0" {...props} />
  ),
  ul: ({ node: _node, ...props }) => (
    <ul className="my-2 list-disc space-y-1 pl-5" {...props} />
  ),
  ol: ({ node: _node, ...props }) => (
    <ol className="my-2 list-decimal space-y-1 pl-5" {...props} />
  ),
  li: ({ node: _node, ...props }) => <li className="pl-0.5" {...props} />,
  blockquote: ({ node: _node, ...props }) => (
    <blockquote
      className="my-3 border-l-2 border-[var(--color-primary)] bg-[rgba(124,140,248,0.07)] py-1.5 pl-3 pr-2 text-[color:var(--color-on-surface-muted)]"
      {...props}
    />
  ),
  a: ({ node: _node, ...props }) => (
    <a
      {...props}
      className="font-medium text-[color:var(--color-primary)] underline decoration-[rgba(124,140,248,0.45)] underline-offset-2 transition-colors hover:text-[color:var(--color-secondary)]"
      target="_blank"
      rel="noopener noreferrer"
    />
  ),
  hr: ({ node: _node, ...props }) => (
    <hr className="my-4 border-0 border-t border-[var(--glass-border)]" {...props} />
  ),
  pre: ({ node: _node, ...props }) => (
    <pre
      className="my-3 max-w-full overflow-x-auto rounded-lg border border-[var(--glass-border)] bg-[rgba(0,0,0,0.3)] p-3 text-xs leading-relaxed"
      {...props}
    />
  ),
  code: ({ node: _node, className, ...props }) => (
    <code
      className={`font-mono text-[0.88em] text-[color:var(--color-cta)] ${
        className ? `${className} bg-transparent` : "rounded bg-[rgba(255,255,255,0.07)] px-1 py-0.5"
      }`}
      {...props}
    />
  ),
  table: ({ node: _node, ...props }) => (
    <div className="my-3 max-w-full overflow-x-auto rounded-lg border border-[var(--glass-border)]">
      <table className="w-full min-w-max border-collapse text-left text-xs" {...props} />
    </div>
  ),
  thead: ({ node: _node, ...props }) => (
    <thead className="bg-[rgba(255,255,255,0.06)]" {...props} />
  ),
  th: ({ node: _node, ...props }) => (
    <th
      className="border-b border-r border-[var(--glass-border)] px-3 py-2 font-semibold last:border-r-0"
      {...props}
    />
  ),
  td: ({ node: _node, ...props }) => (
    <td
      className="border-b border-r border-[var(--glass-border)] px-3 py-2 align-top last:border-r-0"
      {...props}
    />
  ),
  input: ({ node: _node, ...props }) => (
    <input {...props} className="mr-1.5 accent-[var(--color-primary)]" disabled />
  ),
  img: ({ alt }) => (
    <span className="text-[color:var(--color-on-surface-muted)]">
      [图片{alt ? `：${alt}` : ""}]
    </span>
  ),
};

export function MarkdownContent({ content }: Props) {
  return (
    <div
      className="min-w-0 break-words text-sm leading-relaxed text-[color:var(--color-on-surface)]"
      style={{ fontFamily: "var(--font-body)" }}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components} skipHtml>
        {content}
      </ReactMarkdown>
    </div>
  );
}
