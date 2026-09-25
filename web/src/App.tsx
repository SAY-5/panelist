import { DeliverySection } from "./ui/delivery";
import { MeasuredRunProvider } from "./ui/demo";
import { FullRunSection } from "./ui/fullrun";
import { GradingSection } from "./ui/grading";
import { Hero } from "./ui/hero";
import { PayoutsSection } from "./ui/payouts";
import { RoutingSection } from "./ui/routing";
import { WorkbenchProvider } from "./ui/workbench";
import { NOT_PORTED, PORTED_SERVICES, PORTED_SERVICE_VERSION } from "./sim";

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
              Panelist is a FastAPI and PostgreSQL service. This page is its {PORTED_SERVICES.join(", ")}{" "}
              logic as of service version {PORTED_SERVICE_VERSION}, ported to TypeScript so it can be read
              and poked at without a database. Not ported:{" "}
              {NOT_PORTED.map(([name, version]) => `${name} (${version})`).join(", ")}. The summary block
              is the {PORTED_SERVICE_VERSION} block, and multi-graded tasks deliver every approved grade
              where the service since 4.0.0 delivers the one its consensus round selected.
            </p>
          </div>
        </footer>
      </WorkbenchProvider>
    </MeasuredRunProvider>
  );
}
