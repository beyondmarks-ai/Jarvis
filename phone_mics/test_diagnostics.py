import asyncio
import struct
import unittest
from unittest.mock import AsyncMock
from phone_mics.diagnostics import AudioHealth, DistanceTest
from phone_mics.hub import Hub, Microphone, Speaker
from phone_mics.test_hub import Detector, Socket, packet, until


class MetricsTests(unittest.TestCase):
    def test_silence_levels_clipping_gaps_and_nonfinite_score(self):
        health = AudioHealth()
        health.audio(bytes(2560), 1)
        self.assertEqual(health.level_dbfs, -96)
        health.audio(struct.pack('<h', 32767)*1280, 2)
        health.prediction(float('nan'), .003)
        data = health.snapshot(.5, now=2.1)
        self.assertEqual(data['frames'],2)
        self.assertEqual(data['gaps'],1)
        self.assertEqual(data['clipped_percent'],100)
        self.assertEqual(data['score'],0)
        self.assertEqual(data['audio_age_ms'],100)
        self.assertNotIn('audio',data)

    def report(self, peaks=(.1,.8,.9,.7), frames=(60,60,60,60)):
        return DistanceTest(5,.5,peaks=list(peaks),frames=list(frames),
                            detections=[p >= .5 for p in peaks]).report()

    def test_three_hits_and_background_required(self):
        self.assertTrue(self.report()['passed'])
        self.assertFalse(self.report(peaks=(.6,.8,.9,.7))['passed'])
        self.assertFalse(self.report(peaks=(.1,.8,.2,.7))['passed'])
        self.assertFalse(self.report(frames=(60,60,10,60))['passed'])

    def test_samples_assigned_to_correct_windows(self):
        test = DistanceTest(3,.5,started=100)
        health = AudioHealth(score=.8)
        for now in (99,100,105,110,115,120):
            test.observe(health,now)
        self.assertEqual(test.frames,[1,1,1,1])
        self.assertEqual(test.peaks,[.8]*4)

    def test_one_lingering_wake_cannot_count_as_three_attempts(self):
        test = DistanceTest(5,.5,started=100)
        high,low = AudioHealth(score=.9),AudioHealth(score=.1)
        for now in (105,109.9,110.1,115.1):
            test.observe(high,now)
        self.assertEqual(test.report()['hits'],[True,False,False])
        test.observe(low,115.2)
        test.observe(low,115.9)
        test.observe(high,116)
        self.assertEqual(test.report()['hits'],[True,False,True])


class TestModeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reply = AsyncMock()
        self.hub = Hub(self.reply)
        self.socket = Socket(self.hub)
        self.hub.speaker = Speaker(self.socket)
        self.peer = Microphone(Socket(),Detector(),'Test phone')
        self.other = Microphone(Socket(),Detector(),'Other phone')
        await self.hub.add(self.peer)
        await self.hub.add(self.other)

    async def asyncTearDown(self):
        await self.hub.close()

    async def test_testing_suppresses_beeps_and_other_phones(self):
        await self.hub.start_test(self.peer,5)
        await asyncio.sleep(.01)
        await self.hub.packet(self.peer,packet(self.hub.epoch,wake=True))
        await self.hub.packet(self.other,packet(self.hub.epoch,wake=True))
        self.assertEqual(self.hub.phase,'testing')
        self.assertEqual(self.hub.distance_test.frames[0],1)
        self.assertEqual(self.socket.events,[])
        self.reply.assert_not_called()
        self.assertIn('test_elapsed',self.hub.metrics(self.peer))
        await self.hub.close()
        self.assertEqual(self.hub.phase,'idle')
        self.assertIsNone(self.hub.distance_test)
        await self.hub.packet(self.other,packet(self.hub.epoch,wake=True))
        await until(lambda:self.hub.phase=='recording')

    async def test_works_without_speaker_and_cancel_before_start(self):
        self.hub.speaker = None
        await self.hub.start_test(self.peer,1)
        await self.hub.close()
        self.assertIsNone(self.hub.distance_test)
        self.assertEqual(self.hub.phase,'idle')

    async def test_active_turn_not_interrupted_by_test(self):
        await self.hub.packet(self.peer,packet(self.hub.epoch,wake=True))
        await until(lambda:self.hub.phase=='recording')
        await self.hub.start_test(self.other,3)
        self.assertEqual(self.hub.owner,self.peer.id)
        self.assertIsNone(self.hub.distance_test)

    async def test_disconnected_test_owner_releases_everyone(self):
        await self.hub.start_test(self.peer,3)
        await asyncio.sleep(.01)
        await self.hub.remove(self.peer)
        self.assertEqual(self.hub.phase,'idle')
        self.assertIsNone(self.hub.owner)
