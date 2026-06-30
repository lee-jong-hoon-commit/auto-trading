"""
Auto Trader - 한국투자증권 + 업비트 AI 자동매매

사용법:
  python main.py           # 대화형 CLI 모드
  python main.py web       # 웹 대시보드 서버 시작
  python main.py start     # CLI에서 봇 자동 시작
  python main.py once      # 1회 분석 후 종료
"""
import asyncio
import sys
import logging
import uvicorn
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich.layout import Layout
from rich.text import Text
from rich import box
from datetime import datetime
from config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/auto_trader.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

console = Console()


def print_banner():
    console.print(Panel.fit(
        "[bold cyan]Auto Trader[/bold cyan]\n"
        "[dim]한국투자증권 + 업비트 AI 자동매매 시스템[/dim]",
        border_style="cyan"
    ))


def print_config_status():
    table = Table(box=box.ROUNDED, show_header=False, border_style="dim")
    table.add_column("항목", style="dim")
    table.add_column("상태")
    table.add_column("메모")

    def status(ok): return "[green]✓ 준비됨[/green]" if ok else "[red]✗ 미설정[/red]"

    table.add_row("한국투자증권 API", status(config.is_kis_ready),
                  f"{'모의투자' if config.KIS_MOCK else '[red]실전투자[/red]'}")
    table.add_row("업비트 API", status(config.is_upbit_ready), "")
    table.add_row("Claude AI", status(config.is_ai_ready), "claude-sonnet-4-6")
    table.add_row("분석 주기", f"[cyan]{config.TRADE_INTERVAL_MINUTES}분[/cyan]", "")
    table.add_row("포지션 관리", "[cyan]AI 자율 결정[/cyan]", "금액·손익절 모두")

    console.print(table)


def print_api_setup_guide():
    guide = """[bold yellow]API 키 설정 가이드[/bold yellow]

1. [bold].env 파일 생성[/bold]
   cp .env.example .env

2. [bold]한국투자증권 KIS API[/bold]
   - https://apiportal.koreainvestment.com 접속
   - 회원가입 → 앱 신청 → App Key / Secret 발급
   - .env의 KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO 입력
   - KIS_MOCK=true 로 먼저 모의투자 테스트

3. [bold]업비트 API[/bold]
   - 업비트 로그인 → 마이페이지 → Open API 관리
   - Access Key / Secret Key 발급 (출금 권한 포함)
   - .env의 UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY 입력

4. [bold]Claude AI API[/bold]
   - https://console.anthropic.com 접속
   - API Keys → Create Key
   - .env의 ANTHROPIC_API_KEY 입력
"""
    console.print(Panel(guide, border_style="yellow"))


async def cli_interactive():
    """대화형 CLI 메뉴"""
    from trader import bot

    print_banner()
    print_config_status()

    if not (config.is_kis_ready or config.is_upbit_ready):
        print_api_setup_guide()
        return

    while True:
        console.print("\n[bold]메뉴[/bold]")
        console.print("  [cyan]1[/cyan] 봇 시작 (자동 주기 실행)")
        console.print("  [cyan]2[/cyan] 1회 즉시 분석/매매")
        console.print("  [cyan]3[/cyan] 포트폴리오 조회")
        console.print("  [cyan]4[/cyan] 거래 내역 조회")
        console.print("  [cyan]5[/cyan] 봇 정지")
        console.print("  [cyan]q[/cyan] 종료\n")

        choice = console.input("[bold cyan]선택 > [/bold cyan]").strip()

        if choice == "1":
            await bot.start_bot()
            console.print(f"[green]봇 시작됨 (주기: {config.TRADE_INTERVAL_MINUTES}분)[/green]")
            console.print("[dim]Ctrl+C로 정지[/dim]")
            try:
                while bot.get_state()["running"]:
                    state = bot.get_state()
                    last = state.get("last_run", "없음")
                    console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')} 실행 중... 마지막 분석: {last}[/dim]",
                                  end="\r")
                    await asyncio.sleep(5)
            except KeyboardInterrupt:
                await bot.stop_bot()
                console.print("\n[yellow]봇 정지됨[/yellow]")

        elif choice == "2":
            console.print("[cyan]분석 중...[/cyan]")
            await bot.run_once()
            state = bot.get_state()
            console.print(f"[green]완료[/green] - {state.get('market_summary', '')}")
            _print_decisions(state.get("last_decisions", []))

        elif choice == "3":
            _print_portfolio()

        elif choice == "4":
            _print_trades()

        elif choice == "5":
            await bot.stop_bot()
            console.print("[yellow]봇 정지됨[/yellow]")

        elif choice == "q":
            console.print("[dim]종료[/dim]")
            break


def _print_decisions(decisions: list):
    if not decisions:
        console.print("[dim]결정 없음[/dim]")
        return
    table = Table(title="AI 매매 결정", box=box.ROUNDED)
    table.add_column("종목")
    table.add_column("액션", justify="center")
    table.add_column("신뢰도", justify="right")
    table.add_column("이유")
    for d in decisions:
        action = d.get("action", "HOLD")
        color = "green" if action == "BUY" else ("red" if action == "SELL" else "dim")
        table.add_row(
            d.get("name", d.get("ticker", "")),
            f"[{color}]{action}[/{color}]",
            f"{d.get('confidence', 0)*100:.0f}%",
            d.get("reason", "")[:60] + "...",
        )
    console.print(table)


def _print_portfolio():
    from trader import kis_client, upbit_client
    if config.is_kis_ready:
        try:
            p = kis_client.get_balance()
            console.print(f"\n[bold]주식 잔고[/bold]: {p['cash']:,}원 (총 {p['total']:,}원)")
            if p["holdings"]:
                t = Table(box=box.SIMPLE)
                t.add_column("종목")
                t.add_column("수량", justify="right")
                t.add_column("평균가", justify="right")
                t.add_column("현재가", justify="right")
                t.add_column("손익률", justify="right")
                for h in p["holdings"]:
                    rate = h["profit_rate"]
                    color = "green" if rate >= 0 else "red"
                    t.add_row(h["name"], str(h["qty"]), f"{h['avg_price']:,.0f}",
                              f"{h['current_price']:,.0f}", f"[{color}]{rate:+.2f}%[/{color}]")
                console.print(t)
        except Exception as e:
            console.print(f"[red]주식 잔고 조회 실패: {e}[/red]")

    if config.is_upbit_ready:
        try:
            p = upbit_client.get_balance()
            console.print(f"\n[bold]코인 잔고[/bold]: {p['cash']:,.0f}원 (총 {p['total']:,.0f}원)")
            if p["holdings"]:
                t = Table(box=box.SIMPLE)
                t.add_column("코인")
                t.add_column("수량", justify="right")
                t.add_column("평균가", justify="right")
                t.add_column("현재가", justify="right")
                t.add_column("손익률", justify="right")
                for h in p["holdings"]:
                    rate = h["profit_rate"]
                    color = "green" if rate >= 0 else "red"
                    t.add_row(h["ticker"], f"{h['qty']:.6f}", f"{h['avg_price']:,.0f}",
                              f"{h['current_price']:,.0f}", f"[{color}]{rate:+.2f}%[/{color}]")
                console.print(t)
        except Exception as e:
            console.print(f"[red]코인 잔고 조회 실패: {e}[/red]")


def _print_trades():
    from trader.executor import get_trade_history
    trades = get_trade_history(20)
    if not trades:
        console.print("[dim]거래 내역 없음[/dim]")
        return
    t = Table(title="최근 거래 내역", box=box.ROUNDED)
    t.add_column("시각")
    t.add_column("구분")
    t.add_column("종목")
    t.add_column("액션")
    t.add_column("가격", justify="right")
    t.add_column("신뢰도", justify="right")
    for tr in trades:
        action = tr.get("action", "")
        color = "green" if action == "BUY" else "red"
        t.add_row(
            tr.get("time", "")[:19],
            "주식" if tr.get("market") == "stock" else "코인",
            tr.get("name", tr.get("ticker", "")),
            f"[{color}]{action}[/{color}]",
            f"{tr.get('price', 0):,.0f}",
            f"{tr.get('confidence', 0)*100:.0f}%",
        )
    console.print(t)


def run_web():
    """웹 서버 실행"""
    from web.app import app
    console.print(f"[cyan]웹 대시보드 시작: http://localhost:{config.WEB_PORT}[/cyan]")
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT, log_level="warning")


async def run_once_and_exit():
    from trader import bot
    print_banner()
    print_config_status()
    console.print("[cyan]1회 분석 실행 중...[/cyan]")
    await bot.run_once()
    state = bot.get_state()
    console.print(f"\n[green]완료[/green]")
    console.print(f"시장 요약: {state.get('market_summary', '')}")
    _print_decisions(state.get("last_decisions", []))


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""

    if arg == "web":
        run_web()
    elif arg == "once":
        asyncio.run(run_once_and_exit())
    elif arg == "start":
        async def _start():
            from trader import bot
            print_banner()
            print_config_status()
            await bot.start_bot()
            try:
                while True:
                    await asyncio.sleep(60)
            except KeyboardInterrupt:
                await bot.stop_bot()
                console.print("\n[yellow]봇 정지됨[/yellow]")
        asyncio.run(_start())
    else:
        asyncio.run(cli_interactive())
