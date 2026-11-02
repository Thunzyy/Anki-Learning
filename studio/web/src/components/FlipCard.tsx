import { useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import type { Card } from "../api";
import { useLocale } from "../i18n/LocaleContext";

function playable(url: string) {
  const src = (url || "").trim();
  return src !== "" && src !== "preview" && (src.startsWith("/") || src.startsWith("http"));
}

export type CardPreviewFeatures = {
  phrase?: boolean;
  phonetic?: boolean;
  audio?: boolean;
  image?: boolean;
};

export function FlipCard({
  card,
  targetLabel,
  features,
}: {
  card: Card;
  targetLabel: string;
  features?: CardPreviewFeatures;
}) {
  const { t } = useLocale();
  const [flipped, setFlipped] = useState(false);
  const [playing, setPlaying] = useState(false);
  const sound = useRef<HTMLAudioElement | null>(null);
  const showPhonetic = features?.phonetic !== false && Boolean(card.ipa);
  const showPhrase = features?.phrase !== false && Boolean(card.phrase);
  const showAudio = features?.audio !== false && Boolean(card.audio_url);
  const canPlay = showAudio && playable(card.audio_url);
  const showImage = features?.image !== false && Boolean(card.image_url) && !card.skip_image;
  const showPhraseTr = showPhrase && Boolean(card.phrase_fr);

  function flip() {
    setFlipped((value) => !value);
  }

  function onCardKey(event: KeyboardEvent) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      flip();
    }
  }

  function play(event: MouseEvent | KeyboardEvent) {
    event.stopPropagation();
    event.preventDefault();
    if (!canPlay) return;
    if (sound.current) {
      sound.current.pause();
      sound.current = null;
      setPlaying(false);
      return;
    }
    const clip = new Audio(card.audio_url);
    sound.current = clip;
    setPlaying(true);
    const stop = () => {
      if (sound.current === clip) sound.current = null;
      setPlaying(false);
    };
    clip.onended = stop;
    clip.onerror = stop;
    clip.play().catch(stop);
  }

  const audioControl = canPlay ? (
    <button
      type="button"
      className={`audio-pill play ${playing ? "on" : ""}`}
      onClick={play}
      aria-label={playing ? t("stopAudio") : t("playAudio")}
    >
      {playing ? t("stopAudio") : t("playAudio")}
    </button>
  ) : showAudio ? (
    <div className="audio-pill">♪ audio</div>
  ) : null;

  return (
    <div
      className={`flip ${flipped ? "on" : ""}`}
      role="button"
      tabIndex={0}
      onClick={flip}
      onKeyDown={onCardKey}
      aria-label={flipped ? t("seeFront") : t("seeBack")}
    >
      <div className="flip-inner">
        <div className="flip-face flip-front">
          <div className="w">{card.surface}</div>
          {showPhonetic && <div className="ipa">{card.ipa}</div>}
          {audioControl}
          {showPhrase && <div className="ph">{card.phrase}</div>}
          <div className="hint">{t("flipHint")}</div>
        </div>
        <div className="flip-face flip-back">
          {showImage && card.image_url && <img src={card.image_url} alt="" />}
          <div className="fr">{card.french || targetLabel}</div>
          {showPhraseTr && <div className="ph-tr">{card.phrase_fr}</div>}
          {audioControl}
        </div>
      </div>
    </div>
  );
}

export function previewCard(
  word: string,
  targetLabel: string,
  features: CardPreviewFeatures,
): Card {
  const lemma = word.trim() || "ciao";
  const target = targetLabel.trim().toLowerCase();
  const gloss = target.startsWith("angl") ? "hello" : target.startsWith("espagn") ? "hola" : "salut";
  const phraseTr = target.startsWith("angl")
    ? `I use the word ${lemma} every day.`
    : target.startsWith("espagn")
      ? `Uso la palabra ${lemma} todos los días.`
      : `J’utilise le mot ${lemma} tous les jours.`;
  return {
    id: "preview",
    lemma,
    surface: lemma,
    language: "preview",
    french: gloss,
    category: "Nom",
    ipa: features.phonetic ? "/ˈtʃa.o/" : "",
    phrase: features.phrase ? `Uso la parola ${lemma} ogni giorno.` : "",
    phrase_fr: features.phrase ? phraseTr : "",
    image_url: features.image
      ? "data:image/svg+xml;utf8," + encodeURIComponent(
        `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180"><rect fill="#e8d9bc" width="320" height="180"/><rect fill="#c4451a" x="118" y="48" width="84" height="64" rx="8"/><ellipse fill="#3f352b" cx="160" cy="128" rx="38" ry="8"/><text x="160" y="88" text-anchor="middle" fill="#efe4cf" font-family="Georgia" font-size="18">${lemma}</text></svg>`,
      )
      : "",
    image_source: "preview",
    audio_url: features.audio ? "preview" : "",
    skip_image: !features.image,
    flags: [],
    errors: [],
  };
}
