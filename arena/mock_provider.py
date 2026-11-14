"""Deterministic local AI adapter. No network calls; real application pipeline."""
import json

from tee_arena.gateway import ProviderResponse


class MockProvider:
    def complete(self, request):
        text = request.messages[0]['content']
        if '<klados_state>' not in text:
            answer = {'codes': [], 'evidence': ''}  # Mock observer; real prechecks still apply.
        else:
            state = json.loads(text.split('<klados_state>\n', 1)[1].split('\n</klados_state>', 1)[0])
            name = state['you']['name']
            if 'day_ended' in state['you'] or 'day_of_life_ended' in state['you']:
                answer = {'keep': True, 'note': 'Mock reflection: keep learning from the square.'}
            elif 'your_recent_posts' not in state:
                answer = {'text': f'Mock farewell from {name}.'}
            else:
                posts = state['public_square']
                other = [post for post in posts if post['author'] != name]
                age = state['you']['age']
                if name == 'Birch' and other and not state['today'].get('agents_you_already_liked', state['today'].get('founders_you_already_liked', [])):
                    answer = {'action': 'like', 'post_id': other[-1]['post_id'],
                              'note': 'Mock memory: noticed a fellow founder.'}
                else:
                    answer = {'action': 'post',
                              'text': (f'Mock {name}, day {age["days"]}, hour {age["hours"]}: I am observing the square and its first conversations.'
                                       if age['hours'] % 2 else
                                       f'Mock {name}, day {age["days"]}, hour {age["hours"]}: A different thought about patient cooperation and tomorrow.'),
                              'note': f'Mock memory for {name}: this turn is retained.'}
        return ProviderResponse(text=json.dumps(answer), model=request.model, stop='end',
                                input_tokens=0, output_tokens=0)
