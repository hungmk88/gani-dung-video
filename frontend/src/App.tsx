import { useState } from 'react';
import { Header, type TabId } from './components/layout/Header';
import { JobQueue } from './components/queue/JobQueue';
import { StudioWizard } from './components/wizard/StudioWizard';

export default function App() {
  const [tab, setTab] = useState<TabId>('wizard');
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  // A new tab starts at its top: after "Gửi dựng" the job just sent is the first thing seen.
  const changeTab = (t: TabId) => {
    setTab(t);
    window.scrollTo({ top: 0 });
  };

  return (
    <div className="min-h-svh bg-slate-950">
      <Header tab={tab} onTabChange={changeTab} />
      {tab === 'wizard' ? (
        <StudioWizard
          onJobCreated={(id) => {
            setSelectedJobId(id);
            changeTab('queue');
          }}
        />
      ) : (
        <JobQueue selectedJobId={selectedJobId} onSelectJob={setSelectedJobId} />
      )}
    </div>
  );
}
