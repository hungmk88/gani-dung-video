import { useState } from 'react';
import { Header, type TabId } from './components/layout/Header';
import { JobQueue } from './components/queue/JobQueue';
import { StudioWizard } from './components/wizard/StudioWizard';

export default function App() {
  const [tab, setTab] = useState<TabId>('wizard');
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);

  return (
    <div className="min-h-svh bg-slate-950">
      <Header tab={tab} onTabChange={setTab} />
      {tab === 'wizard' ? (
        <StudioWizard
          onJobCreated={(id) => {
            setSelectedJobId(id);
            setTab('queue');
          }}
        />
      ) : (
        <JobQueue selectedJobId={selectedJobId} onSelectJob={setSelectedJobId} />
      )}
    </div>
  );
}
