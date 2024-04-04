import styled from '@emotion/styled';

import {EventDataSection} from 'sentry/components/events/eventDataSection';
import LazyLoad from 'sentry/components/lazyLoad';
import LoadingError from 'sentry/components/loadingError';
import {t} from 'sentry/locale';
import {space} from 'sentry/styles/space';
import type {IssueAttachment, Organization, Project} from 'sentry/types';
import type {Event} from 'sentry/types/event';
import {useApiQuery} from 'sentry/utils/queryClient';
import useOrganization from 'sentry/utils/useOrganization';

type Props = {
  event: Event;
  orgId: Organization['id'];
  projectSlug: Project['slug'];
};

function EventRRWebIntegrationContent({orgId, projectSlug, event}: Props) {
  const {
    data: attachmentList,
    isLoading,
    isError,
    refetch,
  } = useApiQuery<IssueAttachment[]>(
    [
      `/projects/${orgId}/${projectSlug}/events/${event.id}/attachments/`,
      {query: {query: 'rrweb'}},
    ],
    {staleTime: 0}
  );

  if (isError) {
    return <LoadingError onRetry={refetch} />;
  }

  if (isLoading) {
    // hide loading indicator
    return null;
  }

  if (!attachmentList?.length) {
    return null;
  }

  const createAttachmentUrl = (attachment: IssueAttachment) => {
    return `/api/0/projects/${orgId}/${projectSlug}/events/${event.id}/attachments/${attachment.id}/?download`;
  };

  return (
    <StyledReplayEventDataSection type="context-replay" title={t('Replay')}>
      <LazyLoad
        component={() => import('./rrwebReplayer')}
        urls={attachmentList.map(createAttachmentUrl)}
      />
    </StyledReplayEventDataSection>
  );
}

export function EventRRWebIntegration(props: Props) {
  const organization = useOrganization();
  const hasReplay = Boolean(
    props.event?.tags?.find(({key}) => key === 'replayId')?.value
  );
  const hasEventAttachmentsFeature = organization.features.includes('event-attachments');

  if (hasReplay || !hasEventAttachmentsFeature) {
    return null;
  }

  return <EventRRWebIntegrationContent {...props} />;
}

const StyledReplayEventDataSection = styled(EventDataSection)`
  overflow: hidden;
  margin-bottom: ${space(3)};
`;
