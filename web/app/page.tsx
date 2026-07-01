import Link from "next/link";

const STEPS = [
  {
    href: "/login",
    title: "1. 家庭访问",
    body: "输入邀请码，连接到自己的家庭空间。",
  },
  {
    href: "/children",
    title: "2. 孩子档案",
    body: "确认或创建孩子资料，后续记录都会归到这个孩子。",
  },
  {
    href: "/log",
    title: "3. 记一笔",
    body: "用一句话记录刚发生的小事，系统会整理成结构化事件。",
  },
  {
    href: "/timeline",
    title: "4. 时间轴",
    body: "查看刚记录的事件，后续也会混排成长信号。",
  },
  {
    href: "/feedback",
    title: "5. 反馈",
    body: "把卡住、看不懂、想要的地方直接告诉我们。",
  },
];

export default function Home() {
  return (
    <div className="space-y-6">
      <section className="rounded-md border border-stone-200 bg-white p-5">
        <h1 className="text-2xl font-semibold">家庭内测</h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-stone-600">
          先把最短闭环跑起来：家庭访问、孩子档案、记录、回看、反馈。
          热度图和周报会随着分析层迁移逐步补齐。
        </p>
        <Link
          href="/login"
          className="mt-4 inline-flex min-h-11 items-center rounded-md bg-stone-900 px-4 text-sm text-white"
        >
          开始
        </Link>
      </section>

      <section className="grid gap-3 sm:grid-cols-2">
        {STEPS.map((step) => (
          <Link
            key={step.href}
            href={step.href}
            className="rounded-md border border-stone-200 bg-white p-4 transition hover:border-stone-400"
          >
            <h2 className="text-sm font-semibold text-stone-900">
              {step.title}
            </h2>
            <p className="mt-1 text-sm leading-5 text-stone-500">{step.body}</p>
          </Link>
        ))}
      </section>
    </div>
  );
}
