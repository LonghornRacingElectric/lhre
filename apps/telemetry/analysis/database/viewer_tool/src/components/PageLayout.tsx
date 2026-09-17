'use client';

import { usePathname } from 'next/navigation';
import Banner from '@/components/Banner';
import { useEffect } from 'react';

export default function PageLayout({ children }) {
  const pathname = usePathname();
  const showBanner = pathname !== '/live-viewer';

  useEffect(() => {
    if (pathname === '/') {
      document.title = 'Telemetry | Home';
    } else if (pathname.startsWith('/driveday')) {
      document.title = 'Telemetry | Create Driveday';
    } else if (pathname.startsWith('/event/new')) {
      document.title = 'Telemetry | Create Event';
    } else if (pathname.startsWith('/tune')) {
      document.title = 'Telemetry | Texas Tune';
    } else if (pathname.startsWith('/live-viewer')) {
      document.title = 'Telemetry | Live Viewer';
    } else if (pathname.startsWith('/replay')) {
      document.title = 'Telemetry | Replay';
    } else {
      document.title = 'Telemetry Webtool';
    }
  }, [pathname]);

  return (
    <>
      {showBanner && <Banner />}
      <main>
        {children}
      </main>
    </>
  );
}
