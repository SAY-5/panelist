import { DeliverySection } from "./ui/delivery";
import { MeasuredRunProvider } from "./ui/demo";
import { FullRunSection } from "./ui/fullrun";
import { GradingSection } from "./ui/grading";
import { Hero } from "./ui/hero";
import { PayoutsSection } from "./ui/payouts";
import { RoutingSection } from "./ui/routing";
import { WorkbenchProvider } from "./ui/workbench";

const REPO = "https://github.com/SAY-5/panelist";

const NAV = [
  ["#routing", "Routing"],
  ["#grading", "Grading"],
  ["#payouts", "Payouts"],
  ["#delivery", "Delivery"],
  ["#run", "Full run"],
];

export default function App() {
  return (
    <MeasuredRunProvider>
      <WorkbenchProvider>
        <a className="skip" href="#main">
          Skip to the demo
        </a>

        <div className="masthead">
          <div className="wrap masthead-inner">
            <span className="wordmark">
              Panelist<span>.</span>
            </span>
            <nav aria-label="Sections">
              {NAV.map(([href, label]) => (
                <a key={href} href={href}>
                  {label}
                </a>
              ))}
            </nav>
          </div>
        </div>

        <Hero />

        <main id="main">
          <div className="wrap">
            <RoutingSection />
            <GradingSection />
            <PayoutsSection />
            <DeliverySection />
            <FullRunSection />
          </div>
        </main>

        <footer className="footer">
          <div className="wrap">
            <div className="footer-links">
              <a href={REPO}>Repository</a>
              <a href={`${REPO}/blob/main/ARCHITECTURE.md`}>ARCHITECTURE.md</a>
              <a href={`${REPO}/blob/main/sim/demo.py`}>sim/demo.py</a>
              <a href={`${REPO}/blob/main/README.md`}>README</a>
            </div>
            <p>
              Panelist is a FastAPI and PostgreSQL service. This page is its routing, grading,
              attention, payout, analytics and delivery logic ported to TypeScript so it can be read
              and poked at without a database. Same rules, same summary block, no network calls.
            </p>
          </div>
        </footer>
      </WorkbenchProvider>
    </MeasuredRunProvider>
  );
}
