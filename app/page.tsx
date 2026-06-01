"use client";

import Image from "next/image";
import { FormEvent, useState } from "react";

const scienceNotes = [
  "Ticks can use electrostatic charge to bridge the tiny gap between vegetation and a passing host.",
  "Fieldstatic is made to reduce static build-up on fabric and gear, lowering one of the forces ticks can exploit.",
  "It is designed as a tick-defense layer, not a generic laundry anti-static shortcut.",
];

const useCases = [
  "Hiking clothes",
  "Dog walking layers",
  "Camp blankets",
  "Garden gear",
];

export default function Home() {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [isJoined, setIsJoined] = useState(false);

  function handleWaitlist(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsJoined(true);
  }

  return (
    <main>
      <section className="hero">
        <nav className="nav" aria-label="Primary navigation">
          <a className="brand" href="#top" aria-label="Fieldstatic home">
            <span className="brand-mark">F</span>
            Fieldstatic
          </a>
          <div className="nav-links">
            <a href="#science">Science</a>
            <a href="#formula">Formula</a>
            <button className="nav-button" onClick={() => setIsModalOpen(true)}>
              Buy spray
            </button>
          </div>
        </nav>

        <div className="hero-grid" id="top">
          <div className="hero-copy">
            <p className="eyebrow">Next-gen vector protection</p>
            <h1>Tick defense for the static age.</h1>
            <p className="hero-text">
              Fieldstatic Electro Shield is an anti-static tick defense spray
              for clothing, gear, and pet-adjacent outdoor routines. It targets
              a lesser-known way ticks reach hosts: electrostatic attraction.
            </p>

            <div className="hero-actions">
              <button className="primary-button" onClick={() => setIsModalOpen(true)}>
                Buy product
              </button>
              <a className="secondary-button" href="#science">
                See the science
              </a>
            </div>

            <div className="proof-strip" aria-label="Product highlights">
              <span>Electrostatic defense</span>
              <span>Invisible dry barrier</span>
              <span>For outdoor gear</span>
            </div>
          </div>

          <div className="product-stage" aria-label="Fieldstatic product bottle">
            <div className="charge-orbit orbit-one" />
            <div className="charge-orbit orbit-two" />
            <Image
              src="/product-cutout.png"
              alt="Fieldstatic Electro Shield tick defense spray bottle"
              width={1122}
              height={1402}
              priority
              className="product-image"
            />
          </div>
        </div>
      </section>

      <section className="science-section" id="science">
        <div className="section-kicker">Science-pop, not scare tactics</div>
        <div className="section-heading">
          <h2>Ticks do not only crawl. They can be pulled.</h2>
          <p>
            Research has shown that ticks can be attracted across small gaps by
            static electric fields carried by animals and people. Fieldstatic
            translates that insight into a practical outdoor spray ritual.
          </p>
        </div>

        <div className="science-grid">
          {scienceNotes.map((note, index) => (
            <article className="science-card" key={note}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <p>{note}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="formula-section" id="formula">
        <div className="formula-panel">
          <div>
            <p className="eyebrow">Electro Shield Formula</p>
            <h2>Not just any anti-static spray.</h2>
          </div>
          <p>
            Standard household anti-static sprays are built for cling, not tick
            exposure. Fieldstatic is positioned for outdoor fabrics, repeatable
            coverage, low-residue wear, and a clear purpose: helping stop ticks
            before they latch onto clothes or pet gear.
          </p>
        </div>

        <div className="use-grid">
          {useCases.map((item) => (
            <div className="use-card" key={item}>
              <span className="bolt">&gt;</span>
              {item}
            </div>
          ))}
        </div>
      </section>

      <section className="cta-section">
        <p className="eyebrow">Order Fieldstatic</p>
        <h2>Ready for fields, trails, parks, and the walk after dinner.</h2>
        <p>
          Add Electro Shield to your outdoor routine and bring electrostatic
          tick defense to the clothes and gear you already use.
        </p>
        <button className="primary-button" onClick={() => setIsModalOpen(true)}>
          Order now
        </button>
      </section>

      <footer className="footer">
        <span>Fieldstatic</span>
        <span>Innovation built in</span>
      </footer>

      {isModalOpen ? (
        <div className="modal-backdrop" role="presentation">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="waitlist-title"
          >
            <button
              className="modal-close"
              aria-label="Close waitlist dialog"
              onClick={() => setIsModalOpen(false)}
            >
              x
            </button>
            {isJoined ? (
              <div className="modal-success">
                <p className="eyebrow">You are on the list</p>
                <h2 id="waitlist-title">We will send the next batch alert.</h2>
                <p>
                  Thanks for joining Fieldstatic. We will keep it useful and
                  only email when there is real launch news.
                </p>
              </div>
            ) : (
              <>
                <p className="eyebrow">Sold out</p>
                <h2 id="waitlist-title">
                  The launch batch sold out faster than expected.
                </h2>
                <p>
                  Leave your email and we will invite you to the waitlist for
                  the next Electro Shield production run.
                </p>
                <form className="waitlist-form" onSubmit={handleWaitlist}>
                  <label htmlFor="email">Email address</label>
                  <input
                    id="email"
                    type="email"
                    placeholder="you@example.com"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    required
                  />
                  <button className="primary-button" type="submit">
                    Join waitlist
                  </button>
                </form>
              </>
            )}
          </section>
        </div>
      ) : null}
    </main>
  );
}
