import Image from "next/image";
import Link from "next/link";
import styles from "./landing.module.css";

export default function Landing() {
  return (
    <main className={styles.wrap}>
      <h1 className={styles.srOnly}>Trackside LAPDOG</h1>
      <Image
        className={styles.logo}
        src="/lapdoglogo2.png"
        alt="Trackside LAPDOG logo: a dachshund's face in a circle"
        width={1500}
        height={1500}
        priority
      />
      <div className={styles.side}>
        <p className={styles.expand}>
          <b>L</b>ap <b>A</b>nalysis &amp; <b>P</b>layback for <b>D</b>rive-day <b>O</b>perations <b>G</b>roup
        </p>
        <p className={styles.about}>Laps, sectors, corners and battery for a drive day, on one timeline you can play back.</p>
        <Link className={styles.open} href="/lapdogviewer">Open the viewer</Link>
      </div>
    </main>
  );
}
