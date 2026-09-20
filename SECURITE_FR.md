# Sécurité du projet

Ce document résume les principales protections mises en place dans ce projet, les principes de conception suivis, et les limites connues à prendre en compte avant tout déploiement en production sur des données réelles sensibles.

Il s'agit d'une **synthèse volontairement résumée** d'un audit de sécurité interne, itératif et approfondi. Les détails techniques permettant de reproduire une faille ne sont pas publiés ici, y compris pour les points déjà corrigés — seuls la nature du risque et le principe du correctif sont donnés.

## Sommaire

- [Principes de conception](#principes-de-conception)
- [Isolation et durcissement des conteneurs](#isolation-et-durcissement-des-conteneurs)
- [Protection contre les documents malveillants](#protection-contre-les-documents-malveillants)
- [Confidentialité des données traitées](#confidentialité-des-données-traitées)
- [Authentification et contrôle d'accès](#authentification-et-contrôle-daccès)
- [Réseau et exposition](#réseau-et-exposition)
- [Gestion des dépendances](#gestion-des-dépendances)
- [Supervision et détection](#supervision-et-détection)
- [Limites connues et risques acceptés](#limites-connues-et-risques-acceptés)
- [Recommandations avant mise en production](#recommandations-avant-mise-en-production)
- [Signaler une vulnérabilité](#signaler-une-vulnérabilité)

## Principes de conception

- **Fail-closed par défaut** : un composant de sécurité indisponible (antivirus, scan) ou un verdict incertain bloque le traitement plutôt que de laisser passer par défaut.
- **Aucune donnée personnelle dans les journaux ou les alertes** : logs d'audit, métriques et alertes n'utilisent jamais de nom de fichier ou d'identifiant en clair — un hash technique sert de référence traçable sans exposer la donnée elle-même.
- **Défense en profondeur** : chaque catégorie de risque (échappement de conteneur, réseau, injection applicative, contenu du document) est traitée par une couche dédiée, pour qu'aucune protection unique ne soit un point de défaillance unique.
- **Vérification empirique systématique** : chaque correctif est testé par un scénario réel reproduit puis rejoué après correction — pas seulement déduit par lecture de code. C'est cette discipline qui a permis de trouver plusieurs failles réelles qu'une revue de code seule n'aurait pas révélées.
- **Traitement 100 % local** : aucune donnée du document traité ne quitte l'infrastructure sur laquelle l'outil est déployé, sauf activation explicite d'une intégration tierce optionnelle (antivirus ICAP, alerting SIEM) que vous contrôlez.

## Isolation et durcissement des conteneurs

- Chaque service tourne en utilisateur non-root, avec un système de fichiers en lecture seule et les capacités Linux réduites au strict nécessaire.
- Le service applicatif principal tourne sous un **profil seccomp personnalisé**, construit par observation réelle des appels système nécessaires à son fonctionnement (plutôt que le profil générique par défaut, bien plus permissif), et vérifié en mode blocage actif — pas seulement en journalisation.
- L'accès au socket Docker (nécessaire au reverse proxy pour la découverte de services) passe par un proxy dédié en lecture seule, jamais un accès direct.
- Une limite de nombre de processus (`pids`) est appliquée à chaque conteneur, pour borner l'impact d'une éventuelle compromission créant des sous-processus en boucle.

## Protection contre les documents malveillants

Chaque document uploadé passe par plusieurs couches de validation indépendantes, avant tout traitement :

- **Validation de structure** : signature binaire et structure interne du fichier vérifiées côté serveur (pas seulement l'extension ou le type déclaré par le navigateur).
- **Protection contre les archives compressées piégées** (« zip bombs ») pour les formats basés sur ZIP (DOCX) : bornes sur la taille décompressée totale, le ratio de compression, et le nombre d'entrées.
- **Protection XXE** (injection d'entités XML externes) : désactivée de façon systématique sur tout parsing XML, vérifiée empiriquement contre les trois familles d'attaque classiques (lecture de fichier local, bombe d'entités, exfiltration réseau).
- **Neutralisation des formules CSV** pouvant être interprétées par un tableur (protection contre l'injection de formule).
- **Bornes de temps et de volume sur la détection elle-même**, pour qu'un document conçu pour être coûteux à analyser ne puisse pas monopoliser le service au détriment des autres utilisateurs.
- **Défense contre les motifs regex à complexité excessive** (ReDoS) : chaque reconnaisseur personnalisé est testé avec des entrées adverses de taille croissante avant mise en production.
- **Scan antivirus optionnel** (protocole ICAP standard), pouvant être branché sur une solution antivirus déjà déployée dans votre infrastructure, exécuté avant tout parsing du document.

## Confidentialité des données traitées

- **Purge complète des métadonnées** à la génération du document anonymisé (auteur, dates de création, logiciel utilisé...), pour les trois formats supportés.
- **Suppression des objets et fragments résiduels** issus de l'état pré-anonymisation qui pourraient autrement subsister de façon non visible dans le fichier de sortie (contenu de page remplacé mais non purgé, entrées d'archive orphelines).
- **Durée de vie limitée** des documents en cours de traitement sur le serveur, avec purge automatique — y compris en cas d'interruption anormale du traitement.
- **Permissions restrictives** sur les fichiers en transit : lecture réservée à l'application elle-même, pas aux autres comptes locaux de la machine hôte.
- **En-têtes HTTP de sécurité** empêchant la mise en cache, côté navigateur, des aperçus de documents non encore anonymisés.

## Authentification et contrôle d'accès

- Authentification entièrement déléguée à un fournisseur OIDC standard (Keycloak, Entra ID, Okta...) — l'application ne stocke ni ne gère elle-même aucun mot de passe.
- Vérification de propriétaire sur les points d'accès sensibles (aperçu et finalisation d'un document), pour qu'un identifiant de tâche connu ou deviné ne suffise pas à accéder au document d'un autre utilisateur.
- Protection contre le contournement du reverse proxy par un composant interne compromis, via un secret partagé vérifié à chaque requête.
- Protection CSRF sur les cookies de session (attribut `SameSite`), vérifiée sur les navigateurs qui n'appliquent pas de politique stricte par défaut.

## Réseau et exposition

- Séparation des réseaux internes : les services qui n'ont pas besoin d'accès sortant vers Internet en sont techniquement privés, même en cas de compromission applicative.
- Seul le reverse proxy est exposé publiquement ; tous les autres services communiquent exclusivement sur des réseaux internes non routables depuis l'extérieur.

## Gestion des dépendances

- Toutes les dépendances (paquets applicatifs et images de base) sont épinglées à une version précise plutôt que de suivre une étiquette flottante comme `latest`, pour un comportement reproductible et pour éviter qu'une mise à jour amont introduise une régression ou une vulnérabilité sans contrôle préalable.
- Un audit complet des dépendances directes et transitives a été mené (paquets applicatifs et images Docker), avec correction des vulnérabilités critiques identifiées disposant d'un correctif amont.
- Un script de veille automatisée existe pour détecter les nouvelles vulnérabilités sur les dépendances réellement utilisées — voir [Limites connues](#limites-connues-et-risques-acceptés).

## Supervision et détection

- Des métriques techniques (volumes traités, causes de rejet, disponibilité des services internes) sont exposées au format Prometheus standard, sans aucune donnée personnelle.
- Un mécanisme d'alerte optionnel peut relayer les événements de sécurité significatifs (antivirus, disponibilité des services, espace disque) vers votre collecteur syslog/SIEM.

## Limites connues et risques acceptés

Ce projet documente honnêtement ce qui reste ouvert plutôt que de le passer sous silence :

- **Certificat TLS** : le déploiement de référence est fourni avec un certificat auto-signé, adapté uniquement à un usage de test. Un certificat de confiance est indispensable avant toute mise en production.
- **Dimensionnement en charge réelle** : le dimensionnement des ressources (calcul, réplicas) n'a pas été validé par un test de charge complet dans toutes les configurations possibles.
- **Contrôle d'accès fin sur le journal d'audit** : tout utilisateur authentifié peut actuellement consulter les métadonnées d'audit (jamais le contenu des documents), sans distinction de rôle. Amélioration prévue, non bloquante pour un usage en confiance restreinte.
- **Rate limiting applicatif** : le rate limiting est actuellement assuré au niveau du reverse proxy plutôt que dans l'application elle-même — suffisant en pratique, mais moins granulaire qu'un contrôle applicatif dédié.
- **Sauvegarde et haute disponibilité** : le déploiement de référence ne couvre pas la sauvegarde automatisée ni la tolérance de panne — à mettre en place selon vos propres exigences de continuité.
- **Veille de vulnérabilités continue** : un outillage existe mais n'est pas encore un processus entièrement automatisé et validé en conditions réelles — une vigilance manuelle périodique reste recommandée en complément.
- **Qualité de détection** : comme tout système basé sur des modèles de reconnaissance d'entités, la détection n'est pas garantie exhaustive à 100 %, en particulier sur des mises en page ou des formulations inhabituelles. C'est pourquoi l'étape de révision humaine avant validation finale est obligatoire, pas optionnelle.
- **Pentest externe** : cette synthèse reflète un audit interne itératif, pas un test d'intrusion mené par un tiers indépendant — fortement recommandé en complément avant tout traitement de données réelles sensibles.

## Recommandations avant mise en production

1. Remplacez le certificat TLS auto-signé par un certificat de confiance.
2. Faites valider le dimensionnement par un test de charge représentatif de votre usage réel.
3. Faites réaliser un test d'intrusion externe, en particulier si des données de santé ou toute autre catégorie de données sensibles au sens réglementaire seront traitées.
4. Activez le scan antivirus (ICAP) et la supervision (syslog/SIEM) si votre infrastructure dispose déjà des briques correspondantes.
5. Mettez en place un processus récurrent de veille sur les vulnérabilités des dépendances, au-delà de l'audit ponctuel déjà réalisé.
6. Faites valider la durée de session et la politique de contrôle d'accès par votre RSSI/DPO, en fonction de votre contexte réglementaire.

## Signaler une vulnérabilité

Si vous découvrez une vulnérabilité dans ce projet, merci de la signaler de manière responsable plutôt que de la publier directement (issue publique, réseaux sociaux...) :

- Contactez-nous en privé à l'adresse indiquée dans le profil du dépôt, avec une description du problème et, si possible, les étapes pour le reproduire.
- Nous nous engageons à accuser réception dans un délai raisonnable et à vous tenir informé de l'avancement du correctif avant toute divulgation publique.
- Une fois un correctif publié, nous documentons la nature du risque et la remédiation dans le [`CHANGELOG.md`](./CHANGELOG.md), dans le même esprit résumé que ce document.

---

*Ce document est une synthèse et sera mis à jour à mesure que de nouveaux audits sont menés ou que des points ouverts sont résolus.*
