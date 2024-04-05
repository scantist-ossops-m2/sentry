import type {BadgeType} from 'sentry/components/featureBadge';
import {t} from 'sentry/locale';

export const MODULE_TITLE = t('Requests');

export const RELEASE_LEVEL: BadgeType = 'alpha';

// NOTE: Awkward typing, but without it `RELEASE_LEVEL` is narrowed and the comparison is not allowed
export const releaseLevelAsBadgeProps = {
  isAlpha: (RELEASE_LEVEL as BadgeType) === 'alpha',
  isBeta: (RELEASE_LEVEL as BadgeType) === 'beta',
  isNew: (RELEASE_LEVEL as BadgeType) === 'new',
};
